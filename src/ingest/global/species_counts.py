"""Per-cell freshwater fish species counts from iNaturalist and GBIF.

Aggregates, not records. The angler profile's `local_richness` only needs to
know which species are recorded around a place and how much recording there
is, so this asks each provider for counts directly:

  * iNaturalist `observations/species_counts`, research grade only. One call
    per cell returns every species with its count, so the 10,000-record
    pagination cap on `observations` never comes into it.
  * GBIF occurrence search with `facet=speciesKey` and `limit=0`: counts per
    species, no records. GBIF's iNaturalist records are research grade only,
    so the GBIF side does not let casual records back in.

Freshwater fish only: both queries are restricted to the families in
`FRESHWATER_FISH_FAMILIES`, resolved to each provider's own family ids. Some
of those families have marine members (gobies, herrings); the cells this is
run on are inland or Great Lakes, where only the freshwater members occur.
A family a provider does not recognise is skipped and reported, never
silently dropped — the two providers' taxonomies differ (Leuciscidae vs
Cyprinidae, Lotidae vs Gadidae), which is why both spellings are listed.

Every HTTP call goes through `_cached_get`. The cache directory is an
argument so a verification run can keep its cache outside the repository.
"""

import hashlib
import json
import logging
import math
import time
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.place_species_count import PlaceSpeciesCount
from src.storage import place_species_counts

_log = logging.getLogger(__name__)

INAT_SOURCE = "iNaturalist"
GBIF_SOURCE = "GBIF"

DEFAULT_CACHE_DIR = Path("data/cache/species_counts")
CACHE_TTL_SECONDS = 7 * 86400
# Grid cell edge in degrees: about 5.5 km north-south, 4 km east-west in
# southern Ontario. Small enough that a cell is "around this water", large
# enough that one cell is one call per provider.
CELL_DEG = 0.05
_REQUEST_GAP_SECONDS = 1.0

_INAT_TAXA_URL = "https://api.inaturalist.org/v1/taxa"
_INAT_COUNTS_URL = "https://api.inaturalist.org/v1/observations/species_counts"
_GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
_GBIF_SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
_GBIF_SPECIES_URL = "https://api.gbif.org/v1/species/{key}"

FRESHWATER_FISH_FAMILIES: tuple[str, ...] = (
    "Acipenseridae",
    "Polyodontidae",
    "Lepisosteidae",
    "Amiidae",
    "Hiodontidae",
    "Anguillidae",
    "Clupeidae",
    "Cyprinidae",
    "Leuciscidae",
    "Xenocyprididae",
    "Acheilognathidae",
    "Tincidae",
    "Catostomidae",
    "Ictaluridae",
    "Esocidae",
    "Umbridae",
    "Osmeridae",
    "Salmonidae",
    "Percopsidae",
    "Gadidae",
    "Lotidae",
    "Fundulidae",
    "Atherinopsidae",
    "Gasterosteidae",
    "Cottidae",
    "Moronidae",
    "Centrarchidae",
    "Percidae",
    "Sciaenidae",
    "Gobiidae",
    "Petromyzontidae",
)


def cells_for_bbox(
    south: float, west: float, north: float, east: float, cell_deg: float = CELL_DEG
) -> list[tuple[str, float, float, float, float]]:
    """Grid cells covering a box, snapped to a fixed global grid.

    Snapping means two overlapping areas fetched on different days share
    cells instead of producing near-duplicates.
    """

    def idx(x: float) -> float:
        # 43.65 / 0.05 is 873.0000000001 in floating point; without rounding
        # ceil() would add a whole row of cells beyond the box.
        return round(x / cell_deg, 9)

    out = []
    for i in range(math.floor(idx(south)), math.ceil(idx(north))):
        for j in range(math.floor(idx(west)), math.ceil(idx(east))):
            s, w = round(i * cell_deg, 6), round(j * cell_deg, 6)
            out.append((f"{i}_{j}", s, w, round(s + cell_deg, 6), round(w + cell_deg, 6)))
    return out


def ingest_area(
    db,
    south: float,
    west: float,
    north: float,
    east: float,
    cache_dir: Path | None = None,
    sources: tuple[str, ...] = (INAT_SOURCE, GBIF_SOURCE),
) -> dict:
    """Fetch and store counts for every cell in a box. Returns what happened.

    The summary carries the numbers that separate "worked" from "silently did
    nothing": unresolved families per provider and non-species rows dropped.
    """
    cache = cache_dir or DEFAULT_CACHE_DIR
    summary: dict = {"cells": 0, "rows": {}, "unresolved_families": {}, "non_species_dropped": 0}

    summary["failed_sources"] = {}
    inat_ids: list[int] = []
    gbif_keys: list[int] = []
    resolvers = {INAT_SOURCE: _inat_family_ids, GBIF_SOURCE: _gbif_family_keys}
    for source in sources:
        try:
            ids, missing = resolvers[source](cache)
        except httpx.HTTPError as exc:
            # One provider being down must not cost the other's counts, and
            # must not read as "no species recorded" either: it is reported.
            summary["failed_sources"][source] = str(exc)
            _log.warning("%s unavailable, no counts fetched from it: %s", source, exc)
            continue
        summary["unresolved_families"][source] = missing
        if source == INAT_SOURCE:
            inat_ids = ids
        else:
            gbif_keys = ids
    for source, missing in summary["unresolved_families"].items():
        if missing:
            _log.warning(
                "%s did not recognise %d of %d freshwater fish families: %s",
                source,
                len(missing),
                len(FRESHWATER_FISH_FAMILIES),
                ", ".join(missing),
            )

    for cell_id, s, w, n, e in cells_for_bbox(south, west, north, east):
        summary["cells"] += 1
        jur = jurisdiction_for_coords((s + n) / 2, (w + e) / 2)
        if INAT_SOURCE in sources and inat_ids:
            rows, dropped = _inat_cell(cache, inat_ids, cell_id, s, w, n, e, jur)
            summary["non_species_dropped"] += dropped
            n_rows = place_species_counts.replace_cell(db, INAT_SOURCE, cell_id, rows)
            summary["rows"][INAT_SOURCE] = summary["rows"].get(INAT_SOURCE, 0) + n_rows
        if GBIF_SOURCE in sources and gbif_keys:
            rows = _gbif_cell(cache, gbif_keys, cell_id, s, w, n, e, jur)
            n_rows = place_species_counts.replace_cell(db, GBIF_SOURCE, cell_id, rows)
            summary["rows"][GBIF_SOURCE] = summary["rows"].get(GBIF_SOURCE, 0) + n_rows

    if summary["non_species_dropped"]:
        _log.warning(
            "iNaturalist: %d genus-or-higher rows dropped from species counts",
            summary["non_species_dropped"],
        )
    return summary


# ── iNaturalist ──────────────────────────────────────────────────────────────


def _inat_family_ids(cache: Path) -> tuple[list[int], list[str]]:
    ids, missing = [], []
    for family in FRESHWATER_FISH_FAMILIES:
        data = _cached_get(cache, _INAT_TAXA_URL, {"q": family, "rank": "family", "per_page": 10})
        match = next(
            (
                t
                for t in data.get("results", [])
                if t.get("name") == family
                and t.get("rank") == "family"
                and t.get("is_active", True)
            ),
            None,
        )
        if match:
            ids.append(int(match["id"]))
        else:
            missing.append(family)
    return ids, missing


def _inat_cell(cache, family_ids, cell_id, s, w, n, e, jur) -> tuple[list[PlaceSpeciesCount], int]:
    params = {
        "taxon_id": ",".join(str(i) for i in sorted(family_ids)),
        "swlat": s,
        "swlng": w,
        "nelat": n,
        "nelng": e,
        "quality_grade": "research",
        "per_page": 500,
    }
    data = _cached_get(cache, _INAT_COUNTS_URL, params)
    rows, dropped = [], 0
    for r in data.get("results", []):
        taxon = r.get("taxon") or {}
        if taxon.get("rank") != "species":
            dropped += 1
            continue
        rows.append(
            PlaceSpeciesCount(
                source=INAT_SOURCE,
                cell_id=cell_id,
                south=s,
                west=w,
                north=n,
                east=e,
                jurisdiction=jur,
                scientific_name=taxon["name"],
                common_name=taxon.get("preferred_common_name"),
                count=int(r.get("count") or 0),
            )
        )
    return rows, dropped


# ── GBIF ─────────────────────────────────────────────────────────────────────


def _gbif_family_keys(cache: Path) -> tuple[list[int], list[str]]:
    keys, missing = [], []
    for family in FRESHWATER_FISH_FAMILIES:
        data = _cached_get(
            cache,
            _GBIF_MATCH_URL,
            {"name": family, "rank": "FAMILY", "kingdom": "Animalia", "verbose": "true"},
        )
        key = _exact_accepted_family(family, data)
        if key is None:
            missing.append(family)
        else:
            keys.append(key)
    return keys, missing


def _exact_accepted_family(family: str, data: dict) -> int | None:
    """The accepted family usage for exactly this name, or None.

    When a name has both an accepted usage and a synonym (Cyprinidae does),
    GBIF's top-level match gives up and returns the kingdom. The accepted
    exact usage is then among the alternatives, so it is looked for there.
    """
    for cand in [data, *data.get("alternatives", [])]:
        if (
            cand.get("matchType") == "EXACT"
            and cand.get("rank") == "FAMILY"
            and cand.get("status") == "ACCEPTED"
            and cand.get("canonicalName", cand.get("scientificName")) == family
            and cand.get("usageKey")
        ):
            return int(cand["usageKey"])
    return None


def _gbif_cell(cache, family_keys, cell_id, s, w, n, e, jur) -> list[PlaceSpeciesCount]:
    params: dict = {
        "decimalLatitude": f"{s},{n}",
        "decimalLongitude": f"{w},{e}",
        "familyKey": sorted(family_keys),
        "occurrenceStatus": "PRESENT",
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "limit": 0,
        "facet": "speciesKey",
        "facetLimit": 1000,
    }
    data = _cached_get(cache, _GBIF_SEARCH_URL, params)
    counts = next(
        (f.get("counts", []) for f in data.get("facets", []) if f.get("field") == "SPECIES_KEY"),
        [],
    )
    rows = []
    for c in counts:
        key = c.get("name")
        sp = _cached_get(cache, _GBIF_SPECIES_URL.format(key=key), {})
        name = sp.get("canonicalName") or sp.get("scientificName")
        if not name:
            continue
        rows.append(
            PlaceSpeciesCount(
                source=GBIF_SOURCE,
                cell_id=cell_id,
                south=s,
                west=w,
                north=n,
                east=e,
                jurisdiction=jur,
                scientific_name=name,
                common_name=sp.get("vernacularName"),
                count=int(c.get("count") or 0),
            )
        )
    return rows


# ── cache ────────────────────────────────────────────────────────────────────


def _cached_get(cache: Path, url: str, params: dict) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:20]
    cache_file = cache / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())

    time.sleep(_REQUEST_GAP_SECONDS)
    response = httpx.get(url, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data
