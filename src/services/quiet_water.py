"""Quiet water: `explore()` results wrapped as spots of kind `quiet_water`.

Quiet water is not a located structure like a river mouth or a dam. It is a
reach of stream with few public observation records, found by `explore()` (the
untapped-potential ranking). What it means is narrow: few records can mean few
anglers, or just few observers, or hard access. It is never a claim about fish,
and it never implies presence or absence (presence vs. pressure, CLAUDE.md).

How the spots are chosen (report 6.3: "top N by explore() within the user's area"):

- **The areas** are the labelled areas the weekly ingest covers
  (`data/ingest_areas.json`) for the build's jurisdiction. That is where the
  observation records that "few" is measured against were collected: outside
  them every reach has few records, so "quiet" would mean "not ingested". The
  user's home is inside one of them.
- **Per area**, `explore()` ranks reaches (balanced mode, stream order 3 and up,
  which is also its culvert defence). Reaches are then spaced at least
  `MIN_SEPARATION_KM` apart: `explore()` scores neighbouring segments of one creek
  almost identically, and fifteen spots on one creek would be one spot fifteen
  times. The first `TIER_2_PER_AREA` are tier 2 (shown by default), the next
  `TIER_3_PER_AREA` tier 3 (street zoom).
- **First Nations waters are left out.** No boundary layer exists, so reaches
  inside the boxes of `data/curation/first_nations_waters_ca_on.toml` are dropped
  before ranking (and counted in the report): the project's rule is that nothing
  is suggested within those waters, and `explore()` itself knows nothing of them.
- **Everyone gets the same spots.** `explore()` runs with no user, so the
  reaches an angler has fished are not removed; that is per-user and is joined
  at read time, never stored on a spot.

The parquet cache (`data/processed/untapped_potential.parquet`, written by
`make compute-untapped`) is required. Without it there are no quiet-water
spots, the build says so at WARNING and in its report, and the API says so in
`meta.notes`: an honest empty, never a crash and never a stale spot kept alive.

Coverage caveats travel with each spot. `explore()` reports whether the access
figure behind a reach was measured (True), is a placeholder because the reach is
outside the mapped footprint (False), or cannot be told because the cached
scores predate coverage tracking (None). That tri-state is kept on the spot as
`signals.access_is_measured` and worded by `spot_labels.caveats_for()`.
"""

import bisect
import logging
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sqlite_utils.db import Database

from src.models.context import EmptyReason, ExploreResult
from src.models.exclusion_zone import ExclusionZone, ExclusionZoneFile
from src.models.ingest_area import IngestArea
from src.models.jurisdiction import JurisdictionCode
from src.models.spot import Spot, SpotSource
from src.services import spot_labels as words
from src.services.stretch_anchors import _coord_key

logger = logging.getLogger(__name__)

# Spots per ingest area: the first five show by default (tier 2), the next ten at
# street zoom (tier 3). Report 6.3.
TIER_2_PER_AREA = 5
TIER_3_PER_AREA = 10
# How many ranked reaches to ask `explore()` for before spacing them out. A creek
# is dozens of near-identical segments, so the list has to be much longer than
# the number kept.
CANDIDATES_PER_AREA = 300
# Two quiet-water spots in one area are at least this far apart. Equal to the
# spot card's radius, so two cards never describe the same water.
MIN_SEPARATION_KM = 2.0
# A reach with no name of its own is titled by the nearest named water within this
# distance ("Quiet water near Black Creek"); with none, it is not published.
NAME_SEARCH_KM = 3.0
# Waters the app does not suggest (First Nations territory), one file per jurisdiction.
EXCLUSIONS_PATH = "data/curation/first_nations_waters_{code}.toml"
STRUCTURE_TYPE = "quiet_reach"
_KM_PER_DEG = 111.32


@dataclass
class QuietWaterBuild:
    """The spots, and the account of how they were chosen."""

    spots: list[Spot] = field(default_factory=list)
    areas_checked: int = 0
    candidates: int = 0
    # The score cache is missing, so nothing could be built. Distinct from "built
    # and found nothing": the remedy is `make compute-untapped`.
    cache_missing: bool = False
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, n: int = 1) -> None:
        if n > 0:
            self.dropped[reason] = self.dropped.get(reason, 0) + n


def build_quiet_water(
    db: Database,
    jurisdiction: JurisdictionCode,
    built_at: str,
    areas: list[IngestArea] | None = None,
    exclusions: list[ExclusionZone] | None = None,
) -> QuietWaterBuild:
    """Quiet-water spots for every ingest area of `jurisdiction`.

    Never raises for a missing cache or an empty area: both are reported.
    """
    from src.services.area_ingest import load_areas
    from src.services.context import explore
    from src.services.untapped_potential import load_cached_untapped

    out = QuietWaterBuild()
    scores = load_cached_untapped()
    if scores is None or scores.empty:
        out.cache_missing = True
        logger.warning(
            "no untapped-potential scores cached: no quiet-water spots built; "
            "run `make compute-untapped`"
        )
        return out

    if areas is None:
        areas = load_areas()
    areas = [a for a in areas if a.jurisdiction == jurisdiction]
    named = _named_reaches(scores)
    if exclusions is None:
        exclusions = load_exclusions(jurisdiction)

    by_id: dict[str, Spot] = {}
    for area in areas:
        out.areas_checked += 1
        response = explore(
            db,
            area.lat,
            area.lng,
            radius_km=area.radius_km,
            limit=CANDIDATES_PER_AREA,
            user_id=0,
        )
        if not response.results:
            # The area has no ranked reaches: say why rather than leaving a silent gap.
            reason = response.empty_reason or EmptyReason.NO_RECORDS_IN_RADIUS
            out.drop(f"area_empty_{reason.value}")
            continue
        out.candidates += len(response.results)
        for spot in _area_spots(
            area, response.results, named, exclusions, built_at, jurisdiction, out
        ):
            kept = by_id.get(spot.spot_id)
            # Areas overlap (Credit and Bronte); a reach in two keeps its better tier.
            if kept is None or spot.tier < kept.tier:
                by_id[spot.spot_id] = spot
            else:
                out.drop("duplicate_across_areas")
    out.spots = sorted(by_id.values(), key=lambda s: (s.tier, s.spot_id))

    if areas and not out.spots:
        logger.warning(
            "no quiet-water spots built from %d area(s): %s",
            len(areas),
            out.dropped or "none ranked",
        )
    return out


def _area_spots(
    area: IngestArea,
    results: list[ExploreResult],
    named: "_NamedReaches",
    exclusions: list[ExclusionZone],
    built_at: str,
    jurisdiction: JurisdictionCode,
    out: QuietWaterBuild,
) -> list[Spot]:
    allowed = []
    for r in results:
        if any(z.contains(r.lat, r.lng) for z in exclusions):
            out.drop("first_nations_waters")
        else:
            allowed.append(r)
    results = allowed
    # `explore()` sorts by score with an unstable sort, so ties come back in any
    # order; fix one so the same cache always gives the same spots.
    ranked = sorted(results, key=lambda r: (-r.score, r.ogf_id))
    chosen: list[ExploreResult] = []
    for r in ranked:
        if len(chosen) >= TIER_2_PER_AREA + TIER_3_PER_AREA:
            break
        if any(_km(r.lat, r.lng, c.lat, c.lng) < MIN_SEPARATION_KM for c in chosen):
            out.drop("too_close_to_a_better_reach")
            continue
        chosen.append(r)

    spots: list[Spot] = []
    for rank, r in enumerate(chosen):
        title = _title(r, named)
        if title is None:
            out.drop("no_name_within_reach")
            continue
        tier = 2 if rank < TIER_2_PER_AREA else 3
        spots.append(
            Spot(
                spot_id=f"quiet_water:{_coord_key(r.lng, r.lat)}",
                kind="quiet_water",
                jurisdiction=jurisdiction,
                lat=r.lat,
                lng=r.lng,
                water_name=r.name,
                title=title,
                tier=tier,
                structure_type=STRUCTURE_TYPE,
                structure_attrs={
                    "ogf_id": r.ogf_id,
                    "stream_order": r.stream_order,
                    "area": area.label,
                    "named": bool(r.name),
                },
                # Separate facts, no score: the ranking number is not published
                # (a leaderboard in all but name), and pressure is not a signal to show.
                signals={
                    "named": bool(r.name),
                    "access_is_measured": r.access_is_measured,
                },
                sources=[
                    SpotSource(kind="explore_ranking", ref=f"ogf:{r.ogf_id}", built_at=built_at)
                ],
                built_at=built_at,
            )
        )
    return spots


def _title(r: ExploreResult, named: "_NamedReaches") -> str | None:
    if r.name:
        return words.QUIET_TITLES["named"].format(name=r.name)
    near = named.nearest(r.lat, r.lng)
    return words.QUIET_TITLES["near"].format(name=near) if near else None


def load_exclusions(jurisdiction: JurisdictionCode) -> list[ExclusionZone]:
    """The waters this jurisdiction's file says not to suggest; none when it has no file."""
    path = Path(EXCLUSIONS_PATH.format(code=jurisdiction.lower().replace("-", "_")))
    if not path.exists():
        logger.info("no exclusion file for %s (%s): nothing excluded", jurisdiction, path)
        return []
    with path.open("rb") as f:
        return ExclusionZoneFile.model_validate(tomllib.load(f)).zones


# ── naming an unnamed reach ───────────────────────────────────────────────────


class _NamedReaches:
    """Named segments by centroid, to name an unnamed reach after the water beside it."""

    def __init__(self, rows: list[tuple[float, float, str]]):
        self._rows = sorted(rows)  # by latitude first
        self._lats = [r[0] for r in self._rows]

    def nearest(self, lat: float, lng: float) -> str | None:
        window = NAME_SEARCH_KM / _KM_PER_DEG
        lo = bisect.bisect_left(self._lats, lat - window)
        hi = bisect.bisect_right(self._lats, lat + window)
        best: tuple[float, str] | None = None
        for rlat, rlng, name in self._rows[lo:hi]:
            d = _km(lat, lng, rlat, rlng)
            if d <= NAME_SEARCH_KM and (best is None or (d, name) < best):
                best = (d, name)
        return best[1] if best else None


def _named_reaches(scores) -> _NamedReaches:
    names = scores["watercourse_name"].fillna("").astype(str).str.strip()
    named = scores[names != ""]
    return _NamedReaches(
        [
            (float(lat), float(lng), name)
            for lat, lng, name in zip(
                named["centroid_lat"], named["centroid_lng"], names[names != ""]
            )
        ]
    )


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat2 - lat1) * _KM_PER_DEG
    dx = (lng2 - lng1) * _KM_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)
