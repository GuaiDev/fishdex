"""Derive a lake's shoreline features from its OHN outline.

Four kinds, all from sources the repo already reads, none of them a fish claim:

- **inlets / outlets** — where a mapped OHN watercourse meets the shore. The
  stream's digitised direction says which: a stream whose *downstream* end sits
  on the shore flows in (an inlet), one whose *upstream* end sits there drains
  the lake (an outlet). A junction whose flow direction OHN has not verified is
  not classified, and is counted, never guessed.
- **islands** — the holes in the outline polygon. A hole is land inside the
  water, straight out of the record.
- **points and peninsulas** — promontories found by convexity: the concave
  notches between the outline and its convex hull, kept when they are narrow and
  deep (a wedge of land), sized into a point (small) or a peninsula (large).

Everything is capped so one lake cannot flood the map, and every feature says
where the shape came from. Point, peninsula and island features are geometry
read off the outline; they are labelled "from the shoreline outline".
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from shapely.geometry import Point, Polygon, mapping, shape
from shapely.geometry.polygon import orient

from src.models.context import ProvenanceKind
from src.models.hydrology import StreamSegment
from src.models.jurisdiction import JurisdictionCode
from src.models.lake_outline import LakeOutline
from src.models.shoreline_feature import ShorelineFeature
from src.services.geometry_utils import M_PER_DEG, approx_area_m2, polygons_of

logger = logging.getLogger(__name__)

# A stream endpoint within this of the shore is a junction with the lake.
STREAM_JUNCTION_TOLERANCE_M = 60.0
# A promontory's wedge must be this many times deeper than its mouth is wide.
MIN_PROMONTORY_RATIO = 0.35
POINT_MIN_DEPTH_M = 120.0
PENINSULA_MIN_DEPTH_M = 400.0
# Smallest island worth a feature, and the caps that bound one lake's list.
MIN_ISLAND_AREA_M2 = 10_000.0
MAX_ISLANDS = 25
MAX_PROMONTORIES = 25
MAX_STREAM_FEATURES = 50
# The outline is simplified this far (degrees, ~55 m) before the convexity work:
# the promontory rule is about the shape of the shore, not its pixel noise.
CONVEXITY_SIMPLIFY_DEG = 0.0005

_OUTLINE_SOURCE = "OHN Waterbody"
_STREAM_SOURCE = "OHN Watercourse"


@dataclass
class ShorelineFeatureSet:
    """The features a build derived, and the counts that say what it set aside.

    The counts are the point: a lake with no outline, an island too small to
    draw, a stream junction OHN has not verified, a list cut off at its cap —
    each is a different reason the map looks thinner than the water.
    """

    features: list[ShorelineFeature] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    lakes_with_outline: int = 0
    lakes_without_outline: int = 0

    def drop(self, reason: str, n: int = 1) -> None:
        if n > 0:
            self.dropped[reason] = self.dropped.get(reason, 0) + n


def derive_shoreline_features(
    outlines: dict[str, LakeOutline],
    water_body_id_by_lake: dict[str, str],
    jurisdiction: JurisdictionCode,
    built_at: str,
    watercourses: Iterable[StreamSegment] | None = None,
) -> ShorelineFeatureSet:
    """Every shoreline feature for every lake that has an outline.

    `water_body_id_by_lake` maps a curated lake name to the water body the build
    gave it (services/water_bodies.py). A lake with no outline contributes
    nothing — we cannot see its shore — and is counted.
    """
    segments = list(watercourses) if watercourses else []
    result = ShorelineFeatureSet()
    for name, outline in outlines.items():
        if not outline.matched or not outline.geometry:
            result.lakes_without_outline += 1
            continue
        wb_id = water_body_id_by_lake.get(name)
        if not wb_id:
            result.lakes_without_outline += 1
            continue
        result.lakes_with_outline += 1
        geom = shape(outline.geometry)
        result.features += _islands(wb_id, geom, outline, jurisdiction, built_at, result)
        result.features += _promontories(wb_id, geom, outline, jurisdiction, built_at, result)
        if segments:
            result.features += _stream_junctions(
                wb_id, geom, outline, segments, jurisdiction, built_at, result
            )
    return result


def _islands(
    wb_id: str,
    geom,
    outline: LakeOutline,
    jurisdiction: JurisdictionCode,
    built_at: str,
    result: ShorelineFeatureSet,
) -> list[ShorelineFeature]:
    rings = []
    for polygon in polygons_of(geom):
        rings.extend(polygon.interiors)
    islands = []
    for ring in rings:
        island = Polygon(ring)
        if not island.is_valid:
            island = island.buffer(0)
        for part in polygons_of(island):
            area = approx_area_m2(part)
            if area >= MIN_ISLAND_AREA_M2:
                islands.append((area, part))
            else:
                result.drop("island_below_min_area")
    islands.sort(key=lambda item: item[0], reverse=True)
    result.drop("island_over_cap", max(0, len(islands) - MAX_ISLANDS))
    out: list[ShorelineFeature] = []
    for area, island in islands[:MAX_ISLANDS]:
        point = island.representative_point()
        out.append(
            ShorelineFeature(
                feature_id=f"{wb_id}:island:{_key(point)}",
                water_body_id=wb_id,
                jurisdiction=jurisdiction,
                kind="island",
                name=None,
                geometry=mapping(orient(island, sign=1.0)),
                source=_OUTLINE_SOURCE,
                source_date=outline.effective_datetime,
                provenance=ProvenanceKind.RECORD,
                derivation="shoreline_outline",
                note=None,
                area_m2=round(area, 1),
                built_at=built_at,
            )
        )
    return out


def _promontories(
    wb_id: str,
    geom,
    outline: LakeOutline,
    jurisdiction: JurisdictionCode,
    built_at: str,
    result: ShorelineFeatureSet,
) -> list[ShorelineFeature]:
    simple = geom.simplify(CONVEXITY_SIMPLIFY_DEG)
    if simple.is_empty:
        simple = geom
    hull = simple.convex_hull
    if hull.area <= simple.area:
        return []  # the shore is already convex: no promontories to find

    bays = hull.difference(simple)
    candidates: list[tuple[float, tuple[float, float], str]] = []
    hull_boundary = hull.boundary
    for bay in polygons_of(bays):
        mouth = bay.boundary.intersection(hull_boundary)
        mouth_len = mouth.length
        if mouth_len <= 0:
            continue
        depth_deg = 0.0
        tip: tuple[float, float] | None = None
        for x, y in bay.exterior.coords:
            d = hull_boundary.distance(Point(x, y))
            if d > depth_deg:
                depth_deg, tip = d, (x, y)
        if tip is None or depth_deg / mouth_len < MIN_PROMONTORY_RATIO:
            continue
        depth_m = depth_deg * M_PER_DEG
        if depth_m >= PENINSULA_MIN_DEPTH_M:
            kind = "peninsula"
        elif depth_m >= POINT_MIN_DEPTH_M:
            kind = "point"
        else:
            continue
        candidates.append((depth_m, tip, kind))

    candidates.sort(key=lambda item: item[0], reverse=True)
    result.drop("promontory_over_cap", max(0, len(candidates) - MAX_PROMONTORIES))
    out: list[ShorelineFeature] = []
    for depth_m, (x, y), kind in candidates[:MAX_PROMONTORIES]:
        out.append(
            ShorelineFeature(
                feature_id=f"{wb_id}:{kind}:{_key(Point(x, y))}",
                water_body_id=wb_id,
                jurisdiction=jurisdiction,
                kind=kind,
                name=None,
                geometry=mapping(Point(x, y)),
                source=_OUTLINE_SOURCE,
                source_date=outline.effective_datetime,
                provenance=ProvenanceKind.INFERENCE,
                derivation="shoreline_outline",
                note=None,
                length_m=round(depth_m, 1),
                built_at=built_at,
            )
        )
    return out


def _stream_junctions(
    wb_id: str,
    geom,
    outline: LakeOutline,
    segments: list[StreamSegment],
    jurisdiction: JurisdictionCode,
    built_at: str,
    result: ShorelineFeatureSet,
) -> list[ShorelineFeature]:
    boundary = geom.boundary
    tol = STREAM_JUNCTION_TOLERANCE_M / M_PER_DEG
    seen: set[tuple[float, float]] = set()
    out: list[ShorelineFeature] = []
    for seg in segments:
        start = _node(seg.start_node)
        end = _node(seg.end_node)
        if start is None or end is None:
            continue
        d_start = boundary.distance(start)
        d_end = boundary.distance(end)
        if min(d_start, d_end) > tol:
            continue
        touch = end if d_end <= d_start else start
        if not seg.flow_verified:
            result.drop("flow_direction_unverified")
            continue
        key = (round(touch.x, 4), round(touch.y, 4))
        if key in seen:
            continue
        seen.add(key)
        kind = "inlet" if d_end <= d_start else "outlet"
        if len(out) >= MAX_STREAM_FEATURES:
            result.drop("stream_feature_over_cap")
            continue
        out.append(
            ShorelineFeature(
                feature_id=f"{wb_id}:{kind}:{seg.ogf_id}",
                water_body_id=wb_id,
                jurisdiction=jurisdiction,
                kind=kind,
                name=seg.name,
                geometry=mapping(touch),
                source=_STREAM_SOURCE,
                source_date=None,
                provenance=ProvenanceKind.RECORD,
                derivation="watercourse_endpoint",
                note=None,
                built_at=built_at,
            )
        )
    return out


def _node(value: str | None) -> Point | None:
    if not value or "," not in value:
        return None
    try:
        lon, lat = (float(part) for part in value.split(",", 1))
    except ValueError:
        return None
    return Point(lon, lat)


def _key(point: Point) -> str:
    return f"{round(point.x, 5)}_{round(point.y, 5)}"
