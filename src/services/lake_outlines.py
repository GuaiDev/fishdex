"""Match a curated lake name to its OHN Waterbody outline, by position.

A lake the curation file names is resolved to the polygon that draws it. The
matching is deliberately positional, because OHN names repeat across unrelated
water ("Rice Lake" is several ponds) and are bilingual ("Lake Ontario (lac
Ontario)"). The rules, in order:

1. **By name, near a mouth.** If a lake-type polygon near one of the lake's
   curated river mouths carries the curated name (bracket stripped), it is the
   match. This is how a lake is picked when its name is on the record.
2. **Largest lake-type polygon within tolerance.** Otherwise the largest
   lake-type polygon near a mouth is the parent water — the way a bay whose own
   name nobody mapped (Humber Bay, Long Point Bay, Owen Sound) lands on the lake
   it opens off. A small pond mapped at the mouth never wins over the lake.
3. **No match.** The outline stays empty rather than guessed.

For the Great Lakes and Georgian Bay only, the match is then widened to every
same-named lake polygon and unioned: their shore is sliced into several polygons
in the source. An inland lake is never widened — its name can belong to a
different lake, so only the matched polygon is kept.

Tolerance is 5 km, not the ~500 m a mouth-to-shore gap would suggest: a curated
mouth is placed on the river *channel*, and one (Rice Lake's) sits 3.6 km from
the lake polygon. The tolerance is a search radius, and the name rule keeps a
loose radius from picking the wrong water.
"""

import logging
import math
from collections.abc import Callable

from shapely.geometry import MultiPolygon, Point, mapping, shape
from shapely.ops import unary_union

from src.ingest.jurisdictions.ca_on.waterbody import OhnWaterbody
from src.models.lake_outline import MATCH_LARGEST, MATCH_NAME, MATCH_NONE, LakeOutline
from src.services.geometry_utils import M_PER_DEG, polygons_of
from src.services.water_bodies import GREAT_LAKES

logger = logging.getLogger(__name__)

MATCH_TOLERANCE_M = 5000.0

# Standing-water layer types that can be a named lake. "River" polygons (wide
# channels) and "Canal" are excluded: they duplicate the watercourse layer.
LAKE_TYPES = frozenset({"Lake", "Kettle Lake", "Reservoir", "Pond"})

FetchNear = Callable[[float, float, float], list[OhnWaterbody]]
FetchByName = Callable[[str], list[OhnWaterbody]]


def match_lake_outline(
    curated_name: str,
    mouths: list[tuple[float, float]],
    candidates: list[OhnWaterbody],
    tol_m: float = MATCH_TOLERANCE_M,
) -> LakeOutline:
    """Pick the outline for one curated lake from the polygons near its mouths. Pure.

    `candidates` are the polygons a `fetch_near` search already returned within
    `tol_m` of a mouth, so they are all in range; this only decides which one.
    """
    lake_candidates = [c for c in candidates if c.waterbody_type in LAKE_TYPES and c.geometry]
    if not lake_candidates:
        return LakeOutline(
            curated_name=curated_name,
            matched=False,
            match_basis=MATCH_NONE,
            note="no lake polygon is mapped within the search radius of any mouth",
        )

    named = [c for c in lake_candidates if c.name == curated_name]
    if named:
        anchor = max(named, key=lambda c: c.area_m2 or 0.0)
        basis = MATCH_NAME
    else:
        anchor = max(lake_candidates, key=lambda c: c.area_m2 or 0.0)
        basis = MATCH_LARGEST

    distance = _mouth_distance_m(mouths, anchor.geometry)
    note = _note(curated_name, anchor, basis, len(lake_candidates))
    return LakeOutline(
        curated_name=curated_name,
        matched=True,
        geometry=anchor.geometry,
        ogf_ids=[anchor.ogf_id],
        ohn_name=anchor.name,
        effective_datetime=anchor.effective_datetime,
        match_basis=basis,
        distance_m=distance,
        note=note,
    )


def resolve_lake_outlines(
    mouths_by_lake: dict[str, list[tuple[float, float]]],
    fetch_near: FetchNear | None = None,
    fetch_by_name: FetchByName | None = None,
    tol_m: float = MATCH_TOLERANCE_M,
) -> dict[str, LakeOutline]:
    """Resolve every lake name to an outline, fetching the polygons it needs.

    `fetch_near` and `fetch_by_name` default to the cached OHN Waterbody adapter
    and are injected in tests. A name that matches nothing is returned unmatched,
    with a note — never dropped, so the build can report it.
    """
    if fetch_near is None or fetch_by_name is None:
        from src.ingest.jurisdictions.ca_on.waterbody import (
            fetch_waterbodies_by_name,
            fetch_waterbodies_near,
        )

        fetch_near = fetch_near or fetch_waterbodies_near
        fetch_by_name = fetch_by_name or fetch_waterbodies_by_name

    from src.ingest.jurisdictions.ca_on.waterbody import (
        OHN_WATERBODY_LICENCE,
        OHN_WATERBODY_LICENCE_URL,
    )

    out: dict[str, LakeOutline] = {}
    for name, mouths in mouths_by_lake.items():
        candidates: dict[int, OhnWaterbody] = {}
        for lat, lon in mouths:
            for wb in fetch_near(lon, lat, tol_m):
                candidates.setdefault(wb.ogf_id, wb)
        outline = match_lake_outline(name, mouths, list(candidates.values()), tol_m)
        if outline.matched and name.strip().lower() in GREAT_LAKES:
            outline = _widen_great_lake(outline, fetch_by_name)
        if outline.matched:
            outline = _with_licence(outline, OHN_WATERBODY_LICENCE, OHN_WATERBODY_LICENCE_URL)
        out[name] = outline
    unmatched = [o.curated_name for o in out.values() if not o.matched]
    if unmatched:
        logger.warning(
            "no OHN outline matched for %d of %d curated lakes: %s",
            len(unmatched),
            len(out),
            ", ".join(sorted(unmatched)),
        )
    return out


def default_outline_resolver(
    mouths_by_lake: dict[str, list[tuple[float, float]]],
) -> dict[str, LakeOutline]:
    """Resolve lake outlines from the cached OHN Waterbody service. The build's default."""
    return resolve_lake_outlines(mouths_by_lake)


def _with_licence(outline: LakeOutline, licence: str, licence_url: str) -> LakeOutline:
    from dataclasses import replace

    return replace(outline, licence=licence, licence_url=licence_url)


def _widen_great_lake(outline: LakeOutline, fetch_by_name: FetchByName) -> LakeOutline:
    """Union every same-named lake polygon into one outline (Great Lakes are sliced)."""
    pooled = [
        w
        for w in fetch_by_name(outline.curated_name)
        if w.name == outline.curated_name and w.waterbody_type in LAKE_TYPES and w.geometry
    ]
    if len(pooled) <= 1:
        return outline
    geoms = [shape(w.geometry) for w in pooled]
    union = unary_union(geoms)
    polygons = polygons_of(union)
    if not polygons:
        return outline

    merged = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    geometry = mapping(merged)
    ogf_ids = sorted({w.ogf_id for w in pooled})
    note = (
        f"{outline.note} Widened to {len(pooled)} same-named polygons and unioned, "
        "because the source slices this lake."
    )
    return LakeOutline(
        curated_name=outline.curated_name,
        matched=True,
        geometry=geometry,
        ogf_ids=ogf_ids,
        ohn_name=outline.ohn_name,
        effective_datetime=outline.effective_datetime,
        match_basis=outline.match_basis,
        distance_m=outline.distance_m,
        note=note,
    )


def _mouth_distance_m(mouths: list[tuple[float, float]], geometry: dict) -> float | None:
    """Approximate metres from the nearest mouth to the outline (0 when inside)."""
    if not mouths or not geometry:
        return None
    outline = shape(geometry)
    best = math.inf
    for lat, lon in mouths:
        best = min(best, outline.distance(Point(lon, lat)))
    if best == math.inf:
        return None
    return round(best * M_PER_DEG, 1)


def _note(curated_name: str, anchor: OhnWaterbody, basis: str, n_candidates: int) -> str:
    if basis == MATCH_NAME:
        return (
            f"matched OHN polygon {anchor.ogf_id} by name; "
            f"{n_candidates} lake-type polygons were near a mouth"
        )
    return (
        f"no OHN polygon near a mouth is named {curated_name!r}; "
        f"took the largest lake-type polygon ({anchor.ogf_id}) of {n_candidates} in range"
    )
