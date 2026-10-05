"""TRCA Regional Watershed Monitoring Program fish community data (CA-ON).

The first systematic fish survey source in the project. Single-pass
electrofishing under the Ontario Stream Assessment Protocol at fixed stations
across nine Toronto-region watersheds, resurveyed on a roughly three-year
cycle since 2000. Published as CSV on TRCA's CKAN portal.

Why this source and not MNRF's: MNRF Broadscale Monitoring fish community
records live in an internal database (fishnetv3) with no public API, and Fish
ON-Line is UI-only. See the 1l reality check in CLAUDE.md. TRCA is the
documented alternative, and as of 2026-10-05 the portal responds (CLAUDE.md
recorded it as unresponsive in May 2026 — that note is stale).

What makes it different from everything already ingested: counts and weights
at a named station on a known date. That is abundance, and repeat visits to
the same station are a time series. It is also the only source here from which
a real ABSENCE can be read — the species not in a station's visit were not
caught at known effort, which presence-only data can never tell you.

Note: SAR records are removed from TRCA's public release, so this corpus
under-represents listed species by design. That is a property of the source,
not a gap to patch.

Coordinates are published as UTM zone 17N, not decimal degrees. The conversion
is the one place this adapter can be silently wrong: a bad datum assumption
shifts stations into the wrong watershed while every row still looks valid.
"""

import csv
import logging
import time
from datetime import date, datetime
from pathlib import Path

import httpx
from pyproj import Transformer

from src.ingest.discovery import check_resource_discovery
from src.models.fish_survey import FishSurveyRecord
from src.services.species_mapping import common_to_scientific

logger = logging.getLogger(__name__)

_PORTAL = "https://data.trca.ca"
_PACKAGE = "rwmp-fish-community-data"
_PACKAGE_SHOW = f"{_PORTAL}/api/3/action/package_show"
_RAW_DIR = Path("data/raw/trca")
_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
_PROGRAM = "TRCA RWMP"
_PROTOCOL = "OSAP single-pass electrofishing"

# TRCA publishes UTM northing/easting with a zone in UTMDatum (17 for the
# Toronto region). Cached per zone: building a Transformer is not free and the
# parse calls this once per row.
_TRANSFORMERS: dict[int, Transformer] = {}


def _transformer(zone: int) -> Transformer:
    if zone not in _TRANSFORMERS:
        # NAD83 / UTM zone N -> WGS84 lat/lng. always_xy keeps the argument
        # order (easting, northing) rather than the CRS's declared axis order,
        # which is the classic way to get coordinates silently transposed.
        _TRANSFORMERS[zone] = Transformer.from_crs(
            f"EPSG:{26900 + zone}", "EPSG:4326", always_xy=True
        )
    return _TRANSFORMERS[zone]


def utm_to_latlng(easting: float, northing: float, zone: int) -> tuple[float, float] | None:
    """Convert UTM to (lat, lng), or None if the result is not plausibly Ontario.

    The bounds check is the guard against a wrong zone or transposed
    easting/northing: both failure modes produce a real number rather than an
    error, so without it a station lands in Kansas and nothing complains.
    """
    try:
        lng, lat = _transformer(zone).transform(easting, northing)
    except Exception as exc:
        logger.debug("UTM transform failed for %s,%s zone %s: %s", easting, northing, zone, exc)
        return None
    if not (41.0 <= lat <= 57.0 and -96.0 <= lng <= -74.0):
        return None
    return float(lat), float(lng)


def resolve_csv_url() -> str | None:
    """Find the fish community CSV on the CKAN package.

    Resolved from the catalogue rather than hardcoded so a re-published
    resource is picked up, and routed through check_resource_discovery so a
    rename is loud instead of yielding a cheerful zero.
    """
    try:
        resp = httpx.get(
            _PACKAGE_SHOW,
            params={"id": _PACKAGE},
            headers={"User-Agent": _USER_AGENT},
            timeout=60,
        )
        resp.raise_for_status()
        resources = resp.json().get("result", {}).get("resources", [])
    except Exception as exc:
        logger.warning("TRCA: package_show failed for %s: %s", _PACKAGE, exc)
        return None

    names = [r.get("name") or r.get("url", "") for r in resources]
    matches = [
        r
        for r in resources
        if (r.get("format") or "").upper() == "CSV"
        and "fish" in ((r.get("name") or "") + (r.get("url") or "")).lower()
    ]
    check_resource_discovery(
        source=f"TRCA {_PACKAGE}",
        matched=len(matches),
        candidates=names,
        matcher="format == CSV and 'fish' in name or url",
    )
    if not matches:
        return None
    # newest first where TRCA states a year in the name
    matches.sort(key=lambda r: r.get("name") or "", reverse=True)
    return matches[0].get("url")


def download_survey_csv(url: str | None = None) -> Path | None:
    """Download the survey CSV. Skips if the local copy is under 30 days old."""
    url = url or resolve_csv_url()
    if not url:
        return None

    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = _RAW_DIR / "rwmp_fish_community.csv"
    if path.exists() and (time.time() - path.stat().st_mtime) < _TTL_SECONDS:
        logger.info("TRCA fish CSV is fresh, skipping download")
        return path

    with httpx.stream(
        "GET", url, follow_redirects=True, headers={"User-Agent": _USER_AGENT}, timeout=180
    ) as resp:
        resp.raise_for_status()
        with path.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
    logger.info("Downloaded TRCA fish CSV to %s", path)
    return path


def parse_survey_records(csv_path: Path, source_url: str | None = None) -> list[FishSurveyRecord]:
    """Parse the TRCA CSV into FishSurveyRecord models.

    Every dropped row is counted and reported. A survey row carries a real
    count, so discarding one silently loses abundance that cannot be
    reconstructed — the failure class CLAUDE.md tracks.
    """
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    records: list[FishSurveyRecord] = []
    dropped = {"no_station": 0, "no_species": 0, "bad_coords": 0}
    unmapped_species: set[str] = set()
    # (station, visit, species) -> index into records, for merging replicates
    seen: dict[tuple[str, str, str], int] = {}
    n_merged = 0

    for row in rows:
        station = (row.get("StationName") or "").strip()
        species = (row.get("Common_Name") or "").strip()
        if not station:
            dropped["no_station"] += 1
            continue
        if not species:
            dropped["no_species"] += 1
            continue

        lat = lng = None
        northing = _as_float(row.get("UTMNorthing"))
        easting = _as_float(row.get("UTMEasting"))
        zone = _as_int(row.get("UTMDatum"))
        if northing is not None and easting is not None and zone:
            converted = utm_to_latlng(easting, northing, zone)
            if converted is None:
                dropped["bad_coords"] += 1
            else:
                lat, lng = converted

        visit = _as_date(row.get("VisitDate"))
        year = _as_int(row.get("SampleYear")) or (visit.year if visit else None)

        sci = common_to_scientific(species)
        if sci is None:
            unmapped_species.add(species)

        count = _as_int(row.get("TotalNum"))
        weight = _as_float(row.get("TotalWeight"))
        key = (station.lower(), str(visit or year), species.lower())

        # TRCA publishes one row per replicate, so a station visit can carry
        # two rows for the same species with DIFFERENT numbers (e.g. CC002WM on
        # 2021-06-23: brook stickleback 80 fish / 40.2 g and 58 fish / 38 g).
        # Keying on (station, visit, species) alone silently kept whichever
        # arrived last and threw the other away — losing real catch. There is
        # no replicate column to key on, so replicates are SUMMED: the station
        # visit is the unit that carries effort, and total catch at the station
        # is what an absence is read against. The merge is counted and logged.
        if key in seen:
            prev = records[seen[key]]
            n_merged += 1
            records[seen[key]] = prev.model_copy(
                update={
                    "total_count": _add(prev.total_count, count),
                    "total_weight_g": _add(prev.total_weight_g, weight),
                }
            )
            continue

        seen[key] = len(records)
        records.append(
            FishSurveyRecord(
                record_id=f"trca|{station}|{visit or year}|{species}".lower(),
                survey_program=_PROGRAM,
                station_name=station,
                visit_date=visit,
                sample_year=year,
                watershed=(row.get("Watershed") or "").strip() or None,
                subwatershed=(row.get("SubWatershed") or "").strip() or None,
                lat=lat,
                lng=lng,
                species_common_name=species,
                species_scientific_name=sci,
                total_count=count,
                total_weight_g=weight,
                sampling_protocol=_PROTOCOL,
                source_url=source_url,
            )
        )

    n_dropped = sum(dropped.values())
    if n_dropped:
        share = n_dropped / len(rows) if rows else 0
        emit = logger.warning if share >= 0.01 else logger.info
        emit(
            "TRCA: dropped %d of %d rows (%.1f%%) — %s",
            n_dropped,
            len(rows),
            share * 100,
            ", ".join(f"{k}={v}" for k, v in dropped.items() if v),
        )
    if unmapped_species:
        # Not an error: the count is still real and the row is kept. But an
        # unmapped name cannot be cross-referenced with iNat or GBIF, so it is
        # worth naming rather than leaving as a silent None.
        logger.warning(
            "TRCA: %d common names did not map to a scientific name and were kept unmapped: %s",
            len(unmapped_species),
            ", ".join(sorted(unmapped_species)[:12]),
        )
    if n_merged:
        logger.info(
            "TRCA: merged %d replicate rows into %d station-visit-species totals "
            "(the source publishes one row per pass)",
            n_merged,
            len(records),
        )
    logger.info("TRCA: parsed %d survey records from %d rows", len(records), len(rows))
    return records


def _add(a: float | int | None, b: float | int | None):
    """Sum two optional numbers, keeping None only when both are absent.

    None means "not published", which is not zero. Treating it as zero would
    invent a count of nothing where the source stated nothing at all."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def _as_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    f = _as_float(value)
    return int(f) if f is not None else None


def _as_date(value: object) -> date | None:
    raw = (str(value) if value is not None else "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None
