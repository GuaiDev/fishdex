"""Bathymetry features derived from LIO depth contours: drop-offs and humps.

Pure geometry, so the tests hand the service synthetic contours and read the
features back. Nothing here touches the network or a database.
"""

import pytest
from pydantic import ValidationError
from shapely.geometry import Polygon, mapping
from shapely.geometry.polygon import orient

from src.models.bathymetry import BathymetryContour, BathymetryIndex
from src.models.lake_outline import MATCH_NAME, LakeOutline
from src.services.bathymetry import (
    MAX_DROP_OFFS,
    MAX_HUMPS,
    MIN_HUMP_AREA_M2,
    LakeBathymetry,
    derive_bathymetry_features,
    fetch_lake_bathymetry,
    match_index,
)

JURISDICTION = "CA-ON"
BUILT = "2026-10-14T00:00:00Z"


def _outline(shell, name="Test Lake", matched=True):
    geom = orient(Polygon(shell), 1.0)
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


def _ring(minx, miny, maxx, maxy):
    return [
        [minx, miny],
        [maxx, miny],
        [maxx, maxy],
        [minx, maxy],
        [minx, miny],
    ]


def _index(ogf_id, shell, year=None, method=None, line_ind=True):
    return BathymetryIndex(
        ogf_id=ogf_id,
        geometry={"type": "Polygon", "coordinates": [shell]},
        survey_year=year,
        survey_method=method,
        bathymetry_line_ind=line_ind,
    )


def _contour(ogf_id, depth, coords, date=None, method=None):
    return BathymetryContour(
        ogf_id=ogf_id,
        depth_m=depth,
        geometry={"type": "LineString", "coordinates": coords},
        survey_date=date,
        survey_method=method,
    )


# A lake and a survey that cover the same square.
BASE = _ring(-0.02, -0.02, 0.02, 0.02)
LAKE = _outline(BASE)
SURVEY = _index(500, BASE, year=1970, method="Transect")


def _derive(contours, index=SURVEY, outline=LAKE, wb_id="lake:test-lake"):
    from src.services.bathymetry import LakeBathymetry

    name = outline.curated_name
    result = derive_bathymetry_features(
        {name: outline},
        {name: wb_id},
        JURISDICTION,
        BUILT,
        {name: LakeBathymetry(index, list(contours))},
    )
    return result


# ── matching a survey to a lake ──────────────────────────────────────────────


def test_match_index_picks_the_most_overlapping_survey():
    from shapely.geometry import shape

    lake = shape(LAKE.geometry)
    small = _index(1, _ring(-0.001, -0.001, 0.001, 0.001))
    big = _index(2, _ring(-0.02, -0.02, 0.02, 0.02))
    assert match_index(lake, [small, big]).ogf_id == 2


def test_match_index_rejects_a_survey_covering_too_little_of_the_lake():
    from shapely.geometry import shape

    # An inland-lake index barely touches a Great Lake outline.
    tiny = _index(1, _ring(-0.0002, -0.0002, 0.0002, 0.0002))
    assert match_index(shape(LAKE.geometry), [tiny]) is None


# ── humps ─────────────────────────────────────────────────────────────────────


def test_a_shallow_ring_inside_a_deep_one_is_a_hump():
    deep = _contour(1, -6.0, _ring(-0.015, -0.015, 0.015, 0.015))
    shallow = _contour(2, -3.0, _ring(-0.005, -0.005, 0.005, 0.005))
    result = _derive([deep, shallow])
    humps = [f for f in result.features if f.kind == "hump"]
    assert len(humps) == 1
    hump = humps[0]
    assert hump.geometry["type"] == "Polygon"
    assert hump.provenance == "inference"
    assert hump.derivation == "bathymetry_contours"
    assert hump.area_m2 > MIN_HUMP_AREA_M2
    assert hump.attributes["depth_m"] == -3.0
    assert hump.attributes["depth_change_m"] == 3.0
    assert hump.attributes["survey_year"] is None
    assert hump.water_body_id == "lake:test-lake"


def test_survey_year_comes_only_from_the_contours_own_date():
    deep = _contour(1, -6.0, _ring(-0.015, -0.015, 0.015, 0.015), date="1965-06-01")
    shallow = _contour(2, -3.0, _ring(-0.005, -0.005, 0.005, 0.005), date="1965-06-01")
    hump = next(f for f in _derive([deep, shallow]).features if f.kind == "hump")
    assert hump.attributes["survey_year"] == 1965
    assert hump.attributes["survey_method"] is None


def test_a_contour_shared_by_two_lakes_is_stored_once():
    deep = _contour(1, -6.0, _ring(-0.015, -0.015, 0.015, 0.015))
    shallow = _contour(2, -3.0, _ring(-0.005, -0.005, 0.005, 0.005))
    bathy = LakeBathymetry(SURVEY, [deep, shallow])
    result = derive_bathymetry_features(
        {"A": LAKE, "B": LAKE},
        {"A": "lake:a", "B": "lake:b"},
        JURISDICTION,
        BUILT,
        {"A": bathy, "B": bathy},
    )
    assert sorted(c.ogf_id for c in result.contours) == [1, 2]
    assert {c.water_body_id for c in result.contours} == {"lake:a"}


def test_a_shallow_ring_too_small_to_draw_is_dropped_and_counted():
    deep = _contour(1, -6.0, _ring(-0.015, -0.015, 0.015, 0.015))
    # ~50 m across: below the hump area floor at this latitude.
    tiny = _contour(2, -3.0, _ring(-0.0002, -0.0002, 0.0002, 0.0002))
    result = _derive([deep, tiny])
    assert not [f for f in result.features if f.kind == "hump"]
    assert result.dropped.get("hump_below_min_area") == 1


def test_humps_are_capped_and_the_rest_counted():
    deep = _contour(1, -6.0, _ring(-0.05, -0.05, 0.05, 0.05))
    contours = [deep]
    for i in range(MAX_HUMPS + 3):
        c = -0.04 + i * 0.003
        contours.append(_contour(10 + i, -3.0, _ring(c, c, c + 0.002, c + 0.002)))
    result = _derive(contours)
    assert len([f for f in result.features if f.kind == "hump"]) == MAX_HUMPS
    assert result.dropped.get("hump_over_cap") == 3


def test_contours_that_do_not_nest_make_no_hump():
    a = _contour(1, -6.0, _ring(-0.015, -0.015, -0.005, -0.005))
    b = _contour(2, -3.0, _ring(0.005, 0.005, 0.015, 0.015))
    result = _derive([a, b])
    assert not [f for f in result.features if f.kind == "hump"]


# ── drop-offs ─────────────────────────────────────────────────────────────────


def test_two_contours_falling_steeply_make_a_drop_off():
    shallow = _contour(1, -3.0, [[-0.02, 0.0], [0.02, 0.0]])
    deep = _contour(2, -5.0, [[-0.02, 0.0005], [0.02, 0.0005]])
    result = _derive([shallow, deep])
    drops = [f for f in result.features if f.kind == "drop_off"]
    assert len(drops) == 1
    drop = drops[0]
    assert drop.geometry["type"] == "Point"
    assert drop.provenance == "inference"
    assert drop.derivation == "bathymetry_contours"
    assert drop.attributes["depth_change_m"] == 2.0
    assert drop.attributes["gap_m"] < 100.0
    assert drop.length_m == 2.0


def test_contours_far_apart_make_no_drop_off():
    shallow = _contour(1, -3.0, [[-0.02, 0.0], [0.02, 0.0]])
    deep = _contour(2, -5.0, [[-0.02, 0.01], [0.02, 0.01]])  # ~1.1 km away
    result = _derive([shallow, deep])
    assert not [f for f in result.features if f.kind == "drop_off"]


def test_a_small_fall_between_contours_is_not_a_drop_off():
    shallow = _contour(1, -3.0, [[-0.02, 0.0], [0.02, 0.0]])
    deep = _contour(2, -4.0, [[-0.02, 0.0005], [0.02, 0.0005]])  # only 1 m fall
    result = _derive([shallow, deep])
    assert not [f for f in result.features if f.kind == "drop_off"]


def test_drop_offs_are_capped_and_the_rest_counted():
    contours = []
    # Many parallel pairs, each far enough apart to be its own drop-off.
    for i in range(MAX_DROP_OFFS + 5):
        y = -0.019 + i * 0.0015
        contours.append(_contour(100 + i * 2, -3.0, [[-0.02, y], [0.02, y]]))
        contours.append(_contour(101 + i * 2, -6.0, [[-0.02, y + 0.0003], [0.02, y + 0.0003]]))
    result = _derive(contours)
    assert len([f for f in result.features if f.kind == "drop_off"]) == MAX_DROP_OFFS
    assert result.dropped.get("drop_off_over_cap") == 5


# ── a lake with no contours ───────────────────────────────────────────────────


def test_a_lake_with_no_contours_gets_no_features_and_is_counted():
    result = _derive([])
    assert result.features == []
    assert result.lakes_without_contours == 1
    assert result.lakes_with_contours == 0


def test_a_lake_whose_survey_line_flag_is_false_gets_no_features():
    no_lines = _index(500, BASE, year=1970, line_ind=False)
    result = _derive([], index=no_lines)
    assert result.features == []
    assert result.lakes_without_contours == 1


# ── fetch_lake_bathymetry (injected fetchers, no network) ────────────────────


def test_fetch_returns_contours_inside_the_matched_survey():
    contours = [
        _contour(1, -6.0, _ring(-0.015, -0.015, 0.015, 0.015)),
        _contour(2, -3.0, _ring(-0.005, -0.005, 0.005, 0.005)),
    ]
    lake = fetch_lake_bathymetry(
        LAKE,
        index_fetcher=lambda *_: [SURVEY],
        contour_fetcher=lambda *_: contours,
    )
    assert lake.index.ogf_id == 500
    assert {c.ogf_id for c in lake.contours} == {1, 2}


def test_fetch_drops_contours_outside_the_lake():
    far = _contour(9, -6.0, _ring(1.0, 1.0, 1.01, 1.01))
    lake = fetch_lake_bathymetry(
        LAKE,
        index_fetcher=lambda *_: [SURVEY],
        contour_fetcher=lambda *_: [far],
    )
    assert lake.contours == []


def test_fetch_returns_nothing_for_an_unmatched_outline():
    unmatched = _outline(BASE, matched=False)
    lake = fetch_lake_bathymetry(unmatched, index_fetcher=lambda *_: [SURVEY])
    assert lake.index is None and lake.contours == []


def test_fetch_returns_index_only_when_the_survey_has_no_lines():
    no_lines = _index(500, BASE, line_ind=False)
    lake = fetch_lake_bathymetry(
        LAKE,
        index_fetcher=lambda *_: [no_lines],
        contour_fetcher=lambda *_: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert lake.index.ogf_id == 500
    assert lake.contours == []


# ── the models ────────────────────────────────────────────────────────────────


def test_a_contour_cannot_be_above_the_surface():
    with pytest.raises(ValidationError):
        _contour(1, 3.0, [[0, 0], [1, 1]])
    assert _contour(1, 0.0, [[0, 0], [1, 1]]).depth_m == 0.0


def test_a_contour_geometry_must_be_a_line():
    with pytest.raises(ValidationError):
        BathymetryContour(ogf_id=1, depth_m=-2.0, geometry={"type": "Point", "coordinates": [0, 0]})


def test_an_index_geometry_must_be_a_polygon():
    with pytest.raises(ValidationError):
        BathymetryIndex(ogf_id=1, geometry={"type": "LineString", "coordinates": [[0, 0], [1, 1]]})


def test_an_empty_geometry_is_rejected():
    with pytest.raises(ValidationError):
        BathymetryIndex(ogf_id=1, geometry={"type": "Polygon", "coordinates": []})
    with pytest.raises(ValidationError):
        BathymetryContour(
            ogf_id=1, depth_m=-2.0, geometry={"type": "LineString", "coordinates": []}
        )
