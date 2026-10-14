"""Special-here: species recorded more often at a spot than along the rest of its river.

The comparison is of *frequency*, never count. A species with 30 records at a
well-watched bridge is not special; one that makes up a third of the records
within 2 km of a spot and a twentieth of the records along the rest of the river
section is. Records are presence-only and follow observers as much as fish, so
the result is "recorded more often here", never "lives here" (the wording is
in render.py).

The rules, each a named constant so a test can pin it and a change is visible:

- Both sides need enough records. Below `MIN_RECORDS_HERE` or
  `MIN_RECORDS_ELSEWHERE` there is no result at all, not a weak one.
- A species is named only if it has `MIN_SPECIES_RECORDS_HERE` records here, its
  share here is at least `MIN_SHARE_RATIO` times its share elsewhere, and the
  gap is at least `MIN_SHARE_GAP`.
- Surveys outrank photos: the counts are made from standardised survey records
  when both sides have enough of them, and from sighting records otherwise. The
  two are never mixed in one share, because a haul with known gear and a photo
  are different amounts of effort.
- Each fish counts once per visit: one record per person per day for sightings,
  one per survey haul for surveys. One angler or naturalist logging fifty of a
  fish on a few days would otherwise make that fish "special" at their favourite
  bridge (it did, in the first run on real records: fifty stonecat, one person).
  This is the project's central point applied to the comparison, that records
  measure who was looking as much as what is there. Records with no person or
  date to key on count one each.
- Records whose location is fuzzed (iNaturalist geoprivacy, about 22 km) are not
  "within 2 km" and are left out on both sides.
- Nothing about months or seasons.

"Elsewhere" is the records within `CORRIDOR_KM` of the spot's river section
(its curated stretch) that are not within the card radius of the spot. It
excludes the spot's own records, so a spot cannot be compared with itself.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlite_utils.db import Database

from src.models.context import SpecialHere, SpecialHereSpecies
from src.models.spot import Spot

logger = logging.getLogger(__name__)

# The card's radius for "recorded here". Equal to spot_card.CARD_RADIUS_KM; a
# test keeps them equal so "here" means the same thing in both places.
HERE_RADIUS_KM = 2.0
# How far from the river section's line a record still counts as "along" it.
CORRIDOR_KM = 2.0
# Both sides need at least this many records (of the kind being counted),
# otherwise the line is skipped.
MIN_RECORDS_HERE = 10
MIN_RECORDS_ELSEWHERE = 30
# A named species needs at least this many records here...
MIN_SPECIES_RECORDS_HERE = 3
# ...a share here at least this many times its share elsewhere...
MIN_SHARE_RATIO = 2.0
# ...and a share here at least this much larger (0.05 = five percentage points).
MIN_SHARE_GAP = 0.05
# At most this many species are named.
MAX_SPECIES = 3
# Same window as the records slice (describe -> build_records).
DAYS_BACK = 3650

_KM_PER_DEG = 111.32
# Spacing of the points the river line is thinned or filled to for distance checks.
_LINE_STEP_KM = 0.5
_CELL_LAT = 0.02  # a little over 2 km
_CELL_LNG = 0.03  # a little over 2 km at 45 degrees north


@dataclass(frozen=True)
class _Record:
    species: str
    common_name: str | None
    lat: float
    lng: float
    evidence: str  # "survey" | "sighting"
    # Who looked, and when ("observer|date" or a survey event id), so a visit that
    # logs one fish many times counts it once. None: nothing to key on.
    visit: str | None = None


def special_here(
    db: Database,
    spot: Spot,
    protected: frozenset[str] = frozenset(),
) -> SpecialHere:
    """Species recorded clearly more often at `spot` than along the rest of its river section.

    `protected` is the card's own set of species with a listing signal; they
    are counted in the totals (they are real records) and never named.
    The result is the same for every user: no personal catch is read.
    """
    from src.storage.stretch_anchors import list_segment_lines
    from src.storage.stretches import get_stretch

    if not spot.water_id:
        return SpecialHere(status="no_river_section", radius_km=HERE_RADIUS_KM)
    stretch = get_stretch(db, spot.water_id)
    lines = list_segment_lines(db, spot.water_id) if stretch else []
    points = _line_points([ln.geometry for ln in lines])
    if stretch is None or not points:
        return SpecialHere(status="no_river_section", radius_km=HERE_RADIUS_KM)

    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    pad_lat = CORRIDOR_KM / _KM_PER_DEG
    pad_lng = CORRIDOR_KM / (_KM_PER_DEG * max(0.2, math.cos(math.radians(spot.lat))))
    bbox = (min(lats) - pad_lat, max(lats) + pad_lat, min(lngs) - pad_lng, max(lngs) + pad_lng)
    records = _load_records(db, bbox)

    cells = _index(points)
    here: list[_Record] = []
    elsewhere: list[_Record] = []
    for rec in records:
        if _km(spot.lat, spot.lng, rec.lat, rec.lng) <= HERE_RADIUS_KM:
            here.append(rec)
        elif _near_line(cells, rec.lat, rec.lng):
            elsewhere.append(rec)

    return _compare(here, elsewhere, stretch.river, protected)


def _compare(
    here: list[_Record],
    elsewhere: list[_Record],
    river: str,
    protected: frozenset[str],
) -> SpecialHere:
    """Pick the evidence basis, then the species. Pure: no database, no spot."""
    for evidence in ("survey", "sighting"):
        h = [r for r in here if r.evidence == evidence]
        e = [r for r in elsewhere if r.evidence == evidence]
        if len(h) >= MIN_RECORDS_HERE and len(e) >= MIN_RECORDS_ELSEWHERE:
            return _species_that_stand_out(h, e, river, evidence, protected)
    # Neither basis had enough on both sides. Name the shortfall of the basis the
    # card would have used: surveys when more of the records here are surveys.
    surveys_here = sum(1 for r in here if r.evidence == "survey")
    evidence = "survey" if surveys_here > len(here) - surveys_here else "sighting"
    return _too_few(
        [r for r in here if r.evidence == evidence],
        [r for r in elsewhere if r.evidence == evidence],
        river,
        evidence,
    )


def _too_few(h: list[_Record], e: list[_Record], river: str, evidence: str) -> SpecialHere:
    status = "too_few_records_here" if len(h) < MIN_RECORDS_HERE else "too_few_records_along_river"
    return SpecialHere(
        status=status,
        river=river,
        evidence=evidence,
        radius_km=HERE_RADIUS_KM,
        here_total=len(h),
        elsewhere_total=len(e),
    )


def _species_that_stand_out(
    here: list[_Record],
    elsewhere: list[_Record],
    river: str,
    evidence: str,
    protected: frozenset[str],
) -> SpecialHere:
    here_n = _tally(here)
    else_n = _tally(elsewhere)
    found: list[SpecialHereSpecies] = []
    for key, (count, species, common) in here_n.items():
        if key in protected or count < MIN_SPECIES_RECORDS_HERE:
            continue
        else_count = else_n.get(key, (0, "", None))[0]
        here_share = count / len(here)
        else_share = else_count / len(elsewhere)
        if here_share - else_share < MIN_SHARE_GAP:
            continue
        if else_share > 0 and here_share / else_share < MIN_SHARE_RATIO:
            continue
        found.append(
            SpecialHereSpecies(
                species=species,
                common_name=common,
                here_records=count,
                here_total=len(here),
                elsewhere_records=else_count,
                elsewhere_total=len(elsewhere),
            )
        )
    # Biggest gap in share first; ties by name, so the same records give the same card.
    found.sort(key=lambda s: (-(s.here_share - s.elsewhere_share), s.species.lower()))
    return SpecialHere(
        status="found" if found else "none_stands_out",
        river=river,
        evidence=evidence,
        radius_km=HERE_RADIUS_KM,
        here_total=len(here),
        elsewhere_total=len(elsewhere),
        species=found[:MAX_SPECIES],
    )


def _tally(records: list[_Record]) -> dict[str, tuple[int, str, str | None]]:
    out: dict[str, tuple[int, str, str | None]] = {}
    for r in records:
        key = r.species.strip().lower()
        count, species, common = out.get(key, (0, r.species, r.common_name))
        out[key] = (count + 1, species, common or r.common_name)
    return out


# ── records ───────────────────────────────────────────────────────────────────


def _load_records(db: Database, bbox: tuple[float, float, float, float]) -> list[_Record]:
    """Every precise fish record in the box, labelled survey or sighting.

    The same three sources the records slice reads (iNaturalist, GBIF, fish
    surveys) with the same survey rule: a GBIF row with a sampling protocol or
    an event id came out of a structured survey. The user's own catches are not
    read: a card is the same for everyone.
    """
    lat0, lat1, lng0, lng1 = bbox
    since = (date.today() - timedelta(days=DAYS_BACK)).isoformat()
    tables = set(db.table_names())
    out: list[_Record] = []
    if "observations" in tables:
        rows = db.execute(
            "SELECT species, common_name, lat, lng, observer_id, observed_on FROM observations "
            "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
            "AND COALESCE(is_obscured, 0) = 0 "
            "AND (source != 'iNaturalist' OR observed_on >= ?)",
            [lat0, lat1, lng0, lng1, since],
        ).fetchall()
        out += [_Record(r[0], r[1], r[2], r[3], "sighting", _visit(r[4], r[5])) for r in rows]
    if "gbif_observations" in tables:
        rows = db.execute(
            "SELECT species, common_name, lat, lng, sampling_protocol, event_id, "
            "recorded_by, observed_on FROM gbif_observations "
            "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
            "AND (observed_on IS NULL OR observed_on >= ?)",
            [lat0, lat1, lng0, lng1, since],
        ).fetchall()
        for r in rows:
            if r[4] or r[5]:
                out.append(
                    _Record(r[0], r[1], r[2], r[3], "survey", f"event|{r[5]}" if r[5] else None)
                )
            else:
                out.append(_Record(r[0], r[1], r[2], r[3], "sighting", _visit(r[6], r[7])))
    if "fish_surveys" in tables:
        rows = db.execute(
            "SELECT COALESCE(species_scientific_name, species_common_name), "
            "species_common_name, lat, lng, station_name, visit_date FROM fish_surveys "
            "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            [lat0, lat1, lng0, lng1],
        ).fetchall()
        out += [
            _Record(r[0], r[1], r[2], r[3], "survey", f"event|{r[4]}|{r[5]}") for r in rows if r[0]
        ]
    return _one_per_visit([r for r in out if r.species])


def _visit(who, when) -> str | None:
    return f"{who}|{when}" if who and when else None


def _one_per_visit(records: list[_Record]) -> list[_Record]:
    """Keep the first record of each species in each visit; records with no visit all stay."""
    seen: set[tuple[str, str]] = set()
    out: list[_Record] = []
    for r in records:
        if r.visit is not None:
            key = (r.species.strip().lower(), r.visit)
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


# ── distance to the river line ────────────────────────────────────────────────


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Flat-earth distance, good to well under 1% over the few km this is used for."""
    dy = (lat2 - lat1) * _KM_PER_DEG
    dx = (lng2 - lng1) * _KM_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _line_points(geometries: list[dict]) -> list[tuple[float, float]]:
    """(lat, lng) points along the lines, no more than `_LINE_STEP_KM` apart."""
    points: list[tuple[float, float]] = []
    for geom in geometries:
        coords = geom.get("coordinates") or []
        parts = coords if geom.get("type") == "MultiLineString" else [coords]
        for part in parts:
            prev = None
            for lon, lat, *_ in part:
                if prev is not None:
                    gap = _km(prev[0], prev[1], lat, lon)
                    steps = int(gap // _LINE_STEP_KM)
                    for i in range(1, steps + 1):
                        f = i / (steps + 1)
                        points.append(
                            (prev[0] + (lat - prev[0]) * f, prev[1] + (lon - prev[1]) * f)
                        )
                points.append((lat, lon))
                prev = (lat, lon)
    return points


def _index(points: list[tuple[float, float]]) -> dict[tuple[int, int], list[tuple[float, float]]]:
    cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lat, lng in points:
        cells.setdefault((math.floor(lat / _CELL_LAT), math.floor(lng / _CELL_LNG)), []).append(
            (lat, lng)
        )
    return cells


def _near_line(
    cells: dict[tuple[int, int], list[tuple[float, float]]], lat: float, lng: float
) -> bool:
    ci, cj = math.floor(lat / _CELL_LAT), math.floor(lng / _CELL_LNG)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for plat, plng in cells.get((ci + di, cj + dj), ()):
                if _km(lat, lng, plat, plng) <= CORRIDOR_KM:
                    return True
    return False
