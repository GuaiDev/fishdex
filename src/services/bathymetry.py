"""Derive a lake's drop-offs and humps from its LIO bathymetry contours.

A lake outline says where the water ends; it says nothing about the bottom. For
the ~3,500 Ontario lakes with a digital survey there is a second record — a set
of depth contours, each line labelled with one depth — and from those two things
a person cares about can be read off:

- **drop-offs** — where the bottom falls away quickly. Found where a deeper
  contour runs close to a shallower one: the closest approach of the two lines is
  the drop-off, and how far the bottom falls across that gap is how steep it is.
  A place steeper than `DROP_OFF_MIN_DEPTH_CHANGE_M` over a gap no wider than
  `DROP_OFF_MAX_GAP_M` qualifies; the steepest are kept first.
- **humps** (shoals) — a shallower closed contour sitting inside a deeper one. The
  nesting is what says it is a rise in the bottom, not a bay: walk each closed
  ring's smallest enclosing ring, and keep the ring whose parent is deeper and
  which itself has no shallower ring inside it — the crest of the shoal.

Only contours produce these. A lake with no contours gets none, so a drop-off is
never drawn from an outline alone. Every feature carries the contour OGF_IDs it
came from, the survey year and method ("survey date unknown" when the source has
neither), and the licence. Depth is at the survey's own water level, not today's;
that, and the inferred metre unit, ride in the feature's data attributes, never
on a card. Thresholds are the named constants below.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from shapely.geometry import Point, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from src.ingest.jurisdictions.ca_on.bathymetry import (
    LIO_BATHYMETRY_ATTRIBUTION,
    LIO_BATHYMETRY_LICENCE,
    LIO_BATHYMETRY_LICENCE_URL,
    fetch_bathymetry_contours,
    fetch_bathymetry_indices,
)
from src.models.bathymetry import BathymetryContour, BathymetryIndex
from src.models.context import ProvenanceKind
from src.models.jurisdiction import JurisdictionCode
from src.models.lake_outline import LakeOutline
from src.models.shoreline_feature import ShorelineFeature
from src.services.geometry_utils import M_PER_DEG, approx_area_m2

logger = logging.getLogger(__name__)

# A hump must enclose at least this much water to be worth a feature; the cap
# bounds one lake's list so a contoured lake cannot flood the map.
MIN_HUMP_AREA_M2 = 20_000.0
MAX_HUMPS = 25
# A drop-off needs the bottom to fall at least this far between two contours ...
DROP_OFF_MIN_DEPTH_CHANGE_M = 2.0
# ... that run at most this far apart. Two drop-offs found within
# DROP_OFF_DEDUPE_M of each other are the same slope, kept once (the steepest).
DROP_OFF_MAX_GAP_M = 100.0
DROP_OFF_DEDUPE_M = 150.0
MAX_DROP_OFFS = 40
# A survey index must cover at least this share of a lake's area to be its
# survey, so a small inland-lake index never lands on a Great Lake outline.
MIN_INDEX_OVERLAP_RATIO = 0.1
# The index search is widened by this much (degrees, ~1 km) so a survey that only
# touches the shore is still found.
_INDEX_PAD_DEG = 0.01
# Contours are kept when they meet the surveyed part of the lake, with a hair of
# slack for a shoreline contour digitised a metre off the edge.
_CONTOUR_TOLERANCE_DEG = 1e-5
_SHORE_EPS = 1e-6
_DEPTH_EPS = 1e-6

_SURVEY_SOURCE = "LIO Bathymetry"
_DEPTH_BASIS = "at the survey's own water level; metres inferred from the source"

FetchIndices = Callable[[float, float, float, float], list[BathymetryIndex]]
FetchContours = Callable[[float, float, float, float], list[BathymetryContour]]


@dataclass(frozen=True)
class LakeBathymetry:
    """What the source holds for one lake: its survey index and its contours."""

    index: BathymetryIndex | None
    contours: list[BathymetryContour]


@dataclass
class BathymetryFeatureSet:
    """The drop-offs and humps a build derived, and the counts that say what it set aside.

    The counts are the point: a lake whose outline matched no survey, a survey
    with no contour data, a hump too small to draw, a list cut off at its cap —
    each is a different reason the bottom looks featureless.
    """

    features: list[ShorelineFeature] = field(default_factory=list)
    contours: list[BathymetryContour] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    lakes_with_contours: int = 0
    lakes_without_contours: int = 0

    def drop(self, reason: str, n: int = 1) -> None:
        if n > 0:
            self.dropped[reason] = self.dropped.get(reason, 0) + n


def fetch_lake_bathymetry(
    outline: LakeOutline,
    index_fetcher: FetchIndices | None = None,
    contour_fetcher: FetchContours | None = None,
) -> LakeBathymetry:
    """Fetch and match the bathymetry for one lake, or an empty result.

    The network work — a survey index for the lake's box, then the contours of
    the matched survey — goes through the injected fetchers (the cached LIO
    adapters by default). A lake with no outline, no overlapping survey, or a
    survey that carries only an index contributes nothing.
    """
    if not outline.matched or not outline.geometry:
        return LakeBathymetry(None, [])
    index_fetcher = index_fetcher or fetch_bathymetry_indices
    contour_fetcher = contour_fetcher or fetch_bathymetry_contours
    geom = shape(outline.geometry)

    min_lon, min_lat, max_lon, max_lat = geom.bounds
    indices = index_fetcher(
        min_lon - _INDEX_PAD_DEG,
        min_lat - _INDEX_PAD_DEG,
        max_lon + _INDEX_PAD_DEG,
        max_lat + _INDEX_PAD_DEG,
    )
    index = match_index(geom, indices)
    if index is None:
        return LakeBathymetry(None, [])
    if index.bathymetry_line_ind is False:
        return LakeBathymetry(index, [])
    ib = shape(index.geometry).bounds
    contours = contour_fetcher(ib[0], ib[1], ib[2], ib[3])
    covered = geom.intersection(shape(index.geometry))
    if covered.is_empty:
        covered = geom
    covered = covered.buffer(_CONTOUR_TOLERANCE_DEG)
    kept = [c for c in contours if _contour_overlaps(covered, c)]
    return LakeBathymetry(index, kept)


def match_index(outline_geom, indices: list[BathymetryIndex]) -> BathymetryIndex | None:
    """The survey index covering the most of a lake, or None when none does. Pure."""
    best: BathymetryIndex | None = None
    best_area = 0.0
    for index in indices:
        try:
            poly = shape(index.geometry)
        except Exception:  # noqa: BLE001 - a bad index geometry must not stop the match
            continue
        if poly.is_empty:
            continue
        area = outline_geom.intersection(poly).area
        if area > best_area:
            best_area, best = area, index
    if best is None or outline_geom.area <= 0:
        return None
    if best_area / outline_geom.area < MIN_INDEX_OVERLAP_RATIO:
        return None
    return best


def derive_bathymetry_features(
    outlines: dict[str, LakeOutline],
    water_body_id_by_lake: dict[str, str],
    jurisdiction: JurisdictionCode,
    built_at: str,
    bathymetry: dict[str, LakeBathymetry],
) -> BathymetryFeatureSet:
    """Every drop-off and hump for every lake whose survey has contours.

    Keyed the same way as the shoreline derivation: a lake name to its outline and
    to the water body the build gave it. A lake with no contours contributes
    nothing and is counted; its name is never guessed at.
    """
    result = BathymetryFeatureSet()
    for name, outline in outlines.items():
        if not outline.matched or not outline.geometry:
            continue
        wb_id = water_body_id_by_lake.get(name)
        if not wb_id:
            continue
        lake = bathymetry.get(name)
        contours = lake.contours if lake else []
        index = lake.index if lake else None
        if not contours:
            result.lakes_without_contours += 1
            continue
        result.lakes_with_contours += 1
        for contour in contours:
            contour.water_body_id = wb_id
            if contour.built_at is None:
                contour.built_at = built_at
        result.contours.extend(contours)
        result.features += _humps(wb_id, contours, index, jurisdiction, built_at, result)
        result.features += _drop_offs(wb_id, contours, index, jurisdiction, built_at, result)
    return result


# ── humps ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Ring:
    contour: BathymetryContour
    polygon: Polygon


def _humps(
    wb_id: str,
    contours: list[BathymetryContour],
    index: BathymetryIndex | None,
    jurisdiction: JurisdictionCode,
    built_at: str,
    result: BathymetryFeatureSet,
) -> list[ShorelineFeature]:
    rings = [r for r in (_closed_ring(c) for c in contours) if r is not None]
    if not rings:
        return []
    rings.sort(key=lambda r: r.polygon.area, reverse=True)  # outermost first
    parents = _parents(rings)
    children: dict[int, list[int]] = {}
    for i, parent in enumerate(parents):
        if parent is not None:
            children.setdefault(parent, []).append(i)

    candidates: list[tuple[float, _Ring, _Ring]] = []
    for i, ring in enumerate(rings):
        parent = rings[parents[i]] if parents[i] is not None else None
        if parent is None or parent.contour.depth_m >= ring.contour.depth_m - _DEPTH_EPS:
            continue  # no deeper ring around it: not a rise in the bottom
        if any(
            rings[ch].contour.depth_m > ring.contour.depth_m + _DEPTH_EPS
            for ch in children.get(i, [])
        ):
            continue  # a shallower ring sits inside: this is not the crest
        area = approx_area_m2(ring.polygon)
        if area < MIN_HUMP_AREA_M2:
            result.drop("hump_below_min_area")
            continue
        candidates.append((area, ring, parent))

    candidates.sort(key=lambda item: item[0], reverse=True)
    result.drop("hump_over_cap", max(0, len(candidates) - MAX_HUMPS))
    out: list[ShorelineFeature] = []
    for area, ring, parent in candidates[:MAX_HUMPS]:
        attrs, date = _survey_attributes(
            [ring.contour, parent.contour],
            index,
            {
                "depth_m": round(ring.contour.depth_m, 2),
                "depth_change_m": round(abs(parent.contour.depth_m - ring.contour.depth_m), 2),
                "area_m2": round(area, 1),
            },
        )
        out.append(
            ShorelineFeature(
                feature_id=f"{wb_id}:hump:{_key(ring.polygon.representative_point())}",
                water_body_id=wb_id,
                jurisdiction=jurisdiction,
                kind="hump",
                name=None,
                geometry=mapping(orient(ring.polygon, sign=1.0)),
                source=_SURVEY_SOURCE,
                source_date=date,
                provenance=ProvenanceKind.INFERENCE,
                derivation="bathymetry_contours",
                note=None,
                attributes=attrs,
                area_m2=round(area, 1),
                built_at=built_at,
            )
        )
    return out


def _closed_ring(contour: BathymetryContour) -> _Ring | None:
    """A contour as a closed ring polygon, or None when the line does not close."""
    try:
        geom = shape(contour.geometry)
    except Exception:  # noqa: BLE001 - a bad contour geometry must not stop the pass
        return None
    if geom.is_empty:
        return None
    lines = [geom] if geom.geom_type == "LineString" else list(getattr(geom, "geoms", []))
    if len(lines) != 1 or not lines[0].is_closed or len(lines[0].coords) < 4:
        return None
    polygon = Polygon(lines[0].coords)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area == 0:
        return None
    return _Ring(contour, polygon)


def _parents(rings: list[_Ring]) -> list[int | None]:
    """For each ring, the index of its smallest enclosing ring (rings sorted largest first)."""
    parents: list[int | None] = [None] * len(rings)
    for i, ring in enumerate(rings):
        point = ring.polygon.representative_point()
        best: int | None = None
        best_area: float | None = None
        for j in range(i):
            if rings[j].polygon.contains(point):
                area = rings[j].polygon.area
                if best_area is None or area < best_area:
                    best_area, best = area, j
        parents[i] = best
    return parents


# ── drop-offs ─────────────────────────────────────────────────────────────────


def _drop_offs(
    wb_id: str,
    contours: list[BathymetryContour],
    index: BathymetryIndex | None,
    jurisdiction: JurisdictionCode,
    built_at: str,
    result: BathymetryFeatureSet,
) -> list[ShorelineFeature]:
    lines: list[tuple[BathymetryContour, object]] = []
    for contour in contours:
        if contour.depth_m >= -_SHORE_EPS:
            continue  # the shoreline contour is not a drop-off
        try:
            geom = shape(contour.geometry)
        except Exception:  # noqa: BLE001 - a bad contour geometry must not stop the pass
            continue
        if not geom.is_empty:
            lines.append((contour, geom))
    if len(lines) < 2:
        return []

    gap_deg = DROP_OFF_MAX_GAP_M / M_PER_DEG
    dedupe_deg = DROP_OFF_DEDUPE_M / M_PER_DEG
    tree = STRtree([geom for _, geom in lines])

    candidates: list[tuple[float, BathymetryContour, BathymetryContour, float, float, Point]] = []
    for contour, geom in lines:
        for j in tree.query(geom.buffer(gap_deg)):
            other, other_geom = lines[j]
            if other.depth_m > contour.depth_m - DROP_OFF_MIN_DEPTH_CHANGE_M:
                continue  # not enough fall between the two lines
            dist = geom.distance(other_geom)
            if dist > gap_deg:
                continue
            depth_change = abs(contour.depth_m - other.depth_m)
            gap_m = dist * M_PER_DEG
            p1, p2 = nearest_points(geom, other_geom)
            point = Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)
            slope = depth_change / max(gap_m, 1.0)
            candidates.append((slope, contour, other, depth_change, gap_m, point))

    candidates.sort(key=lambda item: item[0], reverse=True)
    kept: list[tuple[float, BathymetryContour, BathymetryContour, float, float, Point]] = []
    accepted: list[Point] = []
    for candidate in candidates:
        point = candidate[5]
        if any(point.distance(other) <= dedupe_deg for other in accepted):
            continue
        accepted.append(point)
        kept.append(candidate)
    result.drop("drop_off_over_cap", max(0, len(kept) - MAX_DROP_OFFS))

    out: list[ShorelineFeature] = []
    for _, contour, other, depth_change, gap_m, point in kept[:MAX_DROP_OFFS]:
        shallow = contour if contour.depth_m >= other.depth_m else other
        deep = other if contour.depth_m >= other.depth_m else contour
        attrs, date = _survey_attributes(
            [shallow, deep],
            index,
            {
                "depth_m": round(shallow.depth_m, 2),
                "depth_change_m": round(depth_change, 2),
                "gap_m": round(gap_m, 1),
            },
        )
        out.append(
            ShorelineFeature(
                feature_id=f"{wb_id}:drop_off:{_key(point)}",
                water_body_id=wb_id,
                jurisdiction=jurisdiction,
                kind="drop_off",
                name=None,
                geometry=mapping(point),
                source=_SURVEY_SOURCE,
                source_date=date,
                provenance=ProvenanceKind.INFERENCE,
                derivation="bathymetry_contours",
                note=None,
                attributes=attrs,
                length_m=round(depth_change, 2),
                built_at=built_at,
            )
        )
    return out


# ── shared ──────────────────────────────────────────────────────────────────


def _survey_attributes(
    contours: list[BathymetryContour],
    index: BathymetryIndex | None,
    extra: dict,
) -> tuple[dict, str | None]:
    """The contour OGF_IDs, survey year and method, attribution, plus `extra`.

    The year and method fall back to the survey index when the contour lines
    carry none, so a contour is never labelled "unknown" when the survey that
    produced it is dated. The returned date is the contour's own, for source_date.
    """
    date = next((c.survey_date for c in contours if c.survey_date), None)
    year = int(date[:4]) if date else (index.survey_year if index else None)
    method = next((c.survey_method for c in contours if c.survey_method), None)
    if method is None and index is not None:
        method = index.survey_method
    attrs = {
        "contour_ogf_ids": [c.ogf_id for c in contours],
        "survey_year": year,
        "survey_method": method,
        "depth_basis": _DEPTH_BASIS,
        "attribution": LIO_BATHYMETRY_ATTRIBUTION,
        "licence": LIO_BATHYMETRY_LICENCE,
        "licence_url": LIO_BATHYMETRY_LICENCE_URL,
    }
    attrs.update(extra)
    return attrs, date


def _contour_overlaps(covered, contour: BathymetryContour) -> bool:
    try:
        geom = shape(contour.geometry)
    except Exception:  # noqa: BLE001 - a bad contour geometry must not stop the filter
        return False
    return not geom.is_empty and covered.intersects(geom)


def _key(point: Point) -> str:
    return f"{round(point.x, 5)}_{round(point.y, 5)}"
