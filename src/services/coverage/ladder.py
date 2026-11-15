"""The ladder: for every reference feature, the first rung where it stops being on the map.

One ladder for every feature class (report section 1.3). Each rung is a geometry
test with a named remedy:

1. not near any curated stretch      -> missing curation (a product choice)
2. on a stretch, no anchor of type   -> missing data (fix the ingest or add a source)
3. anchor, but no spot               -> detection (fix the spot builder)
4. (placement: S2b)
5. spot built, not on the host       -> deploy (fix the reseed)
6. (filter or zoom: S2b)
7. (rendering: needs a browser)

Only rungs 1-3 and 5 are decided here. Features that clear them are `matched`.
"""

import math
import sqlite3

from src.models.coverage import (
    Cause,
    ClassReport,
    DbProbe,
    FeatureResult,
    ReferenceFeature,
    ReferenceSpec,
)
from src.services.coverage import geometry
from src.services.coverage.probe import table_exists


class _Stretch:
    """A curated stretch's lines and bounding box, for a fast first reject."""

    def __init__(self, stretch_id: str):
        self.stretch_id = stretch_id
        self.lines: list[list[tuple[float, float]]] = []
        self.box: tuple[float, float, float, float] | None = None

    def finish(self) -> None:
        self.box = geometry.bbox_of(self.lines)


def load_stretches(conn: sqlite3.Connection, jurisdiction: str) -> list[_Stretch]:
    """Every curated stretch of the jurisdiction with its lines. Empty if none stored."""
    if not table_exists(conn, "stretch_segment_lines"):
        return []
    by_id: dict[str, _Stretch] = {}
    rows = conn.execute(
        "SELECT stretch_id, geometry_geojson FROM stretch_segment_lines WHERE jurisdiction = ?",
        [jurisdiction],
    )
    for stretch_id, geojson in rows:
        st = by_id.setdefault(stretch_id, _Stretch(stretch_id))
        st.lines.extend(geometry.lines_of(geojson))
    for st in by_id.values():
        st.finish()
    return [s for s in by_id.values() if s.box is not None]


def _nearest_stretch(
    lat: float, lon: float, stretches: list[_Stretch], within_m: float
) -> tuple[_Stretch | None, float]:
    """The closest stretch no further than `within_m`, with its distance. (None, inf) if none."""
    best: _Stretch | None = None
    best_d = math.inf
    for st in stretches:
        padded = geometry.pad_bbox(st.box, lat, within_m)
        if not (padded[0] <= lon <= padded[2] and padded[1] <= lat <= padded[3]):
            continue
        d = geometry.point_to_lines_m(lat, lon, st.lines)
        if d < best_d:
            best, best_d = st, d
    if best_d > within_m:
        return None, math.inf
    return best, best_d


def _anchors(
    conn: sqlite3.Connection, jurisdiction: str, types: list[str]
) -> list[tuple[str, float, float]]:
    if not table_exists(conn, "stretch_anchors"):
        return []
    marks = ", ".join("?" * len(types))
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            f"SELECT anchor_id, lat, lng FROM stretch_anchors "
            f"WHERE jurisdiction = ? AND anchor_type IN ({marks})",
            [jurisdiction, *types],
        )
    ]


def _nearest(lat: float, lon: float, points: list, within_m: float):
    """(item, metres) of the closest (id, lat, lon) point within range, else (None, None)."""
    best, best_d = None, math.inf
    for item in points:
        d = geometry.metres_between(lat, lon, item[1], item[2])
        if d < best_d:
            best, best_d = item, d
    if best is not None and best_d <= within_m:
        return best, best_d
    return None, None


def _coverage_status(conn: sqlite3.Connection, stretch_id: str) -> str | None:
    if not table_exists(conn, "stretch_osm_coverage"):
        return None
    row = conn.execute(
        "SELECT status FROM stretch_osm_coverage WHERE stretch_id = ?", [stretch_id]
    ).fetchone()
    return row[0] if row else None


def check_point_class(
    spec: ReferenceSpec,
    features: list[ReferenceFeature],
    conn: sqlite3.Connection,
    jurisdiction: str,
    *,
    hosted: DbProbe | None = None,
    keep_results: bool = True,
) -> ClassReport:
    """Walk every reference feature down the ladder against the build database."""
    stretches = load_stretches(conn, jurisdiction)
    if not stretches:
        return ClassReport(
            feature_class=spec.feature_class,
            label=spec.label,
            independent=spec.independent,
            reference_total=len(features),
            in_scope=0,
            out_of_scope=0,
            matched=0,
            recall=None,
            min_recall=spec.min_recall,
            by_cause={},
            unmeasured_reason=(
                f"no curated stretches stored for {jurisdiction}; run `make build-stretches`"
            ),
        )

    anchors = _anchors(conn, jurisdiction, spec.anchor_types)
    spots = [(s.spot_id, s.lat, s.lon) for s in _build_spots(conn, jurisdiction, spec.spot_kinds)]
    hosted_ids = {s.spot_id for s in hosted.spots} if hosted is not None else None

    results: list[FeatureResult] = []
    tally: dict[str, int] = {}
    for feat in features:
        result = _walk(feat, spec, stretches, anchors, spots, conn, hosted_ids)
        results.append(result)
        tally[result.cause.value] = tally.get(result.cause.value, 0) + 1

    out_of_scope = tally.get(Cause.MISSING_CURATION.value, 0)
    in_scope = len(features) - out_of_scope
    matched = tally.get(Cause.MATCHED.value, 0)
    return ClassReport(
        feature_class=spec.feature_class,
        label=spec.label,
        independent=spec.independent,
        reference_total=len(features),
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        matched=matched,
        recall=(matched / in_scope) if in_scope else None,
        min_recall=spec.min_recall,
        by_cause=tally,
        results=results if keep_results else [],
    )


def _build_spots(conn: sqlite3.Connection, jurisdiction: str, kinds: list[str]):
    from src.services.coverage.probe import probe_spots

    return probe_spots(conn, jurisdiction, kinds, None)


def _walk(
    feat: ReferenceFeature,
    spec: ReferenceSpec,
    stretches: list[_Stretch],
    anchors: list,
    spots: list,
    conn: sqlite3.Connection,
    hosted_ids: set[str] | None,
) -> FeatureResult:
    base = {"ref_id": feat.ref_id, "name": feat.name, "lat": feat.lat, "lon": feat.lon}

    # 1. Is there curated water here at all?
    stretch, stretch_d = _nearest_stretch(feat.lat, feat.lon, stretches, spec.scope_m)
    if stretch is None:
        return FeatureResult(
            **base,
            cause=Cause.MISSING_CURATION,
            detail=f"no curated stretch within {spec.scope_m:g} m",
        )

    # 2. Does the map hold an anchor of the right type here?
    anchor, anchor_d = _nearest(feat.lat, feat.lon, anchors, spec.anchor_m)
    if anchor is None:
        status = _coverage_status(conn, stretch.stretch_id)
        return FeatureResult(
            **base,
            cause=Cause.MISSING_DATA,
            stretch_id=stretch.stretch_id,
            stretch_distance_m=round(stretch_d, 1),
            detail=(
                f"no {'/'.join(spec.anchor_types)} anchor within {spec.anchor_m:g} m; "
                f"OpenStreetMap coverage for the stretch: {status or 'not recorded'}"
            ),
        )

    # 3. Did the spot builder turn it into a spot?
    spot, spot_d = _nearest(feat.lat, feat.lon, spots, spec.spot_m)
    if spot is None:
        return FeatureResult(
            **base,
            cause=Cause.DETECTION,
            stretch_id=stretch.stretch_id,
            stretch_distance_m=round(stretch_d, 1),
            anchor_distance_m=round(anchor_d, 1),
            detail=f"anchor {anchor[0]} exists but no {'/'.join(spec.spot_kinds)} spot within "
            f"{spec.spot_m:g} m",
        )

    # 5. Is the spot on the host? (Only when a hosted side was supplied.)
    if hosted_ids is not None and spot[0] not in hosted_ids:
        return FeatureResult(
            **base,
            cause=Cause.DEPLOY,
            stretch_id=stretch.stretch_id,
            stretch_distance_m=round(stretch_d, 1),
            anchor_distance_m=round(anchor_d, 1),
            spot_distance_m=round(spot_d, 1),
            detail=f"spot {spot[0]} is in the build but not on the host",
        )

    return FeatureResult(
        **base,
        cause=Cause.MATCHED,
        stretch_id=stretch.stretch_id,
        stretch_distance_m=round(stretch_d, 1),
        anchor_distance_m=round(anchor_d, 1),
        spot_distance_m=round(spot_d, 1),
        detail=f"spot {spot[0]}",
    )
