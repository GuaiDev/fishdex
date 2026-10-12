"""Shoreline features derived from a lake outline: islands, promontories, stream junctions.

Pure geometry, so the tests hand the service a polygon and read the features back.
Nothing here touches the network or a database.
"""

import pytest
from pydantic import ValidationError
from shapely.geometry import Polygon, mapping
from shapely.geometry.polygon import orient

from src.models.hydrology import StreamSegment
from src.models.lake_outline import MATCH_NAME, LakeOutline
from src.models.shoreline_feature import ShorelineFeature
from src.services.shoreline_features import (
    MAX_ISLANDS,
    derive_shoreline_features,
)

JURISDICTION = "CA-ON"
BUILT = "2026-10-12T00:00:00Z"


def outline_of(shell, holes=(), name="Test Lake", matched=True):
    geom = orient(Polygon(shell, holes), 1.0)
    return LakeOutline(
        curated_name=name,
        matched=matched,
        geometry=mapping(geom) if matched else None,
        ogf_ids=[1],
        ohn_name=name,
        effective_datetime="2020-10-07",
        match_basis=MATCH_NAME,
        distance_m=0.0,
        note="",
    )


BASE = [(-0.01, -0.01), (0.01, -0.01), (0.01, 0.01), (-0.01, 0.01), (-0.01, -0.01)]


def derive(outline, wb_id="lake:test-lake", segments=()):
    return derive_shoreline_features(
        {outline.curated_name: outline},
        {outline.curated_name: wb_id},
        JURISDICTION,
        BUILT,
        list(segments),
    )


def seg(ogf_id, name, start, end, verified=True):
    return StreamSegment(
        ogf_id=ogf_id,
        watercourse_type="Stream",
        name=name,
        flow_verified=verified,
        permanency="Permanent",
        flow_classification=None,
        stream_order=3,
        length_m=100.0,
        geom_wkt="LINESTRING (0 0, 1 1)",
        start_node=start,
        end_node=end,
        jurisdiction=JURISDICTION,
        segment_source="ohn",
    )


# ── islands ───────────────────────────────────────────────────────────────────


def test_a_hole_in_the_outline_is_an_island():
    hole = [(-0.002, -0.002), (-0.002, 0.002), (0.002, 0.002), (0.002, -0.002), (-0.002, -0.002)]
    result = derive(outline_of(BASE, [hole]))
    islands = [f for f in result.features if f.kind == "island"]
    assert len(islands) == 1
    assert islands[0].geometry["type"] == "Polygon"
    assert islands[0].provenance == "record"
    assert islands[0].derivation == "shoreline_outline"
    assert islands[0].area_m2 > 0
    assert islands[0].water_body_id == "lake:test-lake"


def test_an_island_smaller_than_the_floor_is_dropped_and_counted():
    tiny = [-0.0002, -0.0002], [-0.0002, 0.0002], [0.0002, 0.0002], [0.0002, -0.0002]
    hole = [*tiny, tiny[0]]
    result = derive(outline_of(BASE, [hole]))
    assert not [f for f in result.features if f.kind == "island"]
    assert result.dropped.get("island_below_min_area") == 1


def test_islands_are_capped_and_the_rest_counted():
    big = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1), (-0.1, -0.1)]
    holes = []
    for i in range(MAX_ISLANDS + 3):
        c = -0.09 + i * 0.006
        holes.append(
            [[c, -0.002], [c, 0.002], [c + 0.002, 0.002], [c + 0.002, -0.002], [c, -0.002]]
        )
    result = derive(outline_of(big, holes))
    assert len([f for f in result.features if f.kind == "island"]) == MAX_ISLANDS
    assert result.dropped.get("island_over_cap") == 3


# ── points and peninsulas ─────────────────────────────────────────────────────


def test_a_deep_narrow_notch_is_a_peninsula():
    shell = [
        (-0.01, -0.01),
        (0.01, -0.01),
        (0.01, -0.001),
        (-0.005, -0.001),
        (-0.005, 0.001),
        (0.01, 0.001),
        (0.01, 0.01),
        (-0.01, 0.01),
        (-0.01, -0.01),
    ]
    result = derive(outline_of(shell))
    promontories = [f for f in result.features if f.kind in ("point", "peninsula")]
    assert promontories and promontories[0].kind == "peninsula"
    assert promontories[0].provenance == "inference"
    assert promontories[0].derivation == "shoreline_outline"
    assert promontories[0].geometry["type"] == "Point"


def test_a_convex_shore_has_no_promontories():
    result = derive(outline_of(BASE))
    assert not [f for f in result.features if f.kind in ("point", "peninsula")]


def test_a_widemouthed_shallow_notch_is_not_a_promontory():
    # shallow relative to its mouth: filtered by the depth:mouth ratio
    shell = [
        (-0.01, -0.01),
        (0.01, -0.01),
        (0.01, -0.001),
        (0.009, -0.001),
        (0.009, 0.001),
        (0.01, 0.001),
        (0.01, 0.01),
        (-0.01, 0.01),
        (-0.01, -0.01),
    ]
    result = derive(outline_of(shell))
    assert not [f for f in result.features if f.kind in ("point", "peninsula")]


# ── stream junctions ──────────────────────────────────────────────────────────


def test_a_stream_whose_downstream_end_touches_is_an_inlet():
    result = derive(
        outline_of(BASE),
        segments=[seg(1, "Inlet Creek", "2.0,0.0", "0.01,0.0")],
    )
    (f,) = [x for x in result.features if x.kind in ("inlet", "outlet")]
    assert f.kind == "inlet"
    assert f.name == "Inlet Creek"
    assert f.derivation == "watercourse_endpoint"
    assert f.provenance == "record"
    assert f.geometry["type"] == "Point"


def test_a_stream_whose_upstream_end_touches_is_an_outlet():
    result = derive(
        outline_of(BASE),
        segments=[seg(2, "Outflow River", "0.01,0.0", "-2.0,0.0")],
    )
    (f,) = [x for x in result.features if x.kind in ("inlet", "outlet")]
    assert f.kind == "outlet"


def test_an_unverified_flow_direction_is_dropped_and_counted():
    result = derive(
        outline_of(BASE),
        segments=[seg(3, "Maybe Creek", "2.0,0.0", "0.01,0.0", verified=False)],
    )
    assert not [x for x in result.features if x.kind in ("inlet", "outlet")]
    assert result.dropped.get("flow_direction_unverified") == 1


def test_a_stream_far_from_the_shore_is_ignored():
    result = derive(
        outline_of(BASE),
        segments=[seg(4, "Far Creek", "5.0,5.0", "4.0,5.0")],
    )
    assert not [x for x in result.features if x.kind in ("inlet", "outlet")]


# ── the set and its counts ────────────────────────────────────────────────────


def test_an_unmatched_outline_contributes_nothing_and_is_counted():
    result = derive(outline_of(BASE, matched=False))
    assert result.features == []
    assert result.lakes_without_outline == 1
    assert result.lakes_with_outline == 0


def test_a_matched_outline_is_counted():
    result = derive(outline_of(BASE))
    assert result.lakes_with_outline == 1
    assert result.lakes_without_outline == 0


def test_features_round_trip_through_the_model():
    result = derive(outline_of(BASE), segments=[seg(1, "Inlet Creek", "2.0,0.0", "0.01,0.0")])
    for f in result.features:
        assert ShorelineFeature.model_validate(f.model_dump()) == f


# ── the model ─────────────────────────────────────────────────────────────────


def test_a_feature_id_without_a_body_is_rejected():
    with pytest.raises(ValidationError):
        ShorelineFeature(
            feature_id="inlet1",
            water_body_id="great_lake:lake-ontario",
            jurisdiction=JURISDICTION,
            kind="inlet",
            geometry={"type": "Point", "coordinates": [0, 0]},
            source="OHN Watercourse",
            derivation="watercourse_endpoint",
            built_at=BUILT,
        )


def test_a_line_is_not_a_shoreline_feature_geometry():
    with pytest.raises(ValidationError):
        ShorelineFeature(
            feature_id="great_lake:lake-ontario#inlet:1",
            water_body_id="great_lake:lake-ontario",
            jurisdiction=JURISDICTION,
            kind="inlet",
            geometry={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            source="OHN Watercourse",
            derivation="watercourse_endpoint",
            built_at=BUILT,
        )
