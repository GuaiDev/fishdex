"""Tests for the OHN Waterbody ingest module (lake and pond polygons).

The fetch goes through the watercourse adapter's cache, so the HTTP mock and the
cache-dir patch point at `hydro_network`, exactly as tests/test_ingest_hydro_network.py does.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HTTP = "src.ingest.jurisdictions.ca_on.hydro_network.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_on.hydro_network._CACHE_DIR"
_FIXTURE = Path(__file__).parent / "fixtures" / "ohn_waterbody_response.json"
_TEST_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "test_tmp"


@pytest.fixture
def cache_dir():
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)
    _TEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield _TEST_CACHE_DIR
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


def _fake_get(payload: dict):
    """A fake httpx.get that always returns `payload` from .json()."""

    def get(*_a, **_k):
        return MagicMock(json=lambda: payload)

    return get


def _capturing_get(payload: dict, sink: dict):
    def get(_url, params=None, **_k):
        sink.update(params or {})
        return MagicMock(json=lambda: payload)

    return get


def _signed_area(ring) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
        area += x1 * y2 - x2 * y1
    return area / 2


# ── names ─────────────────────────────────────────────────────────────────────


def test_strip_bilingual_removes_the_trailing_bracket():
    from src.ingest.jurisdictions.ca_on.waterbody import strip_bilingual

    assert strip_bilingual("Lake Ontario (lac Ontario)") == "Lake Ontario"
    assert strip_bilingual("Rice Lake") == "Rice Lake"
    assert strip_bilingual(None) is None
    assert strip_bilingual("   ") is None
    # a bracket in the middle (not the tail) is part of the name
    assert strip_bilingual("St. Clair River (lower)") == "St. Clair River"


# ── ring → GeoJSON ────────────────────────────────────────────────────────────


def test_one_ring_becomes_a_polygon():
    from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson

    geom = rings_to_geojson([[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]])
    assert geom["type"] == "Polygon"
    assert len(geom["coordinates"]) == 1


def test_a_small_ring_inside_a_big_one_is_a_hole():
    from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson

    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]
    geom = rings_to_geojson([outer, hole])
    assert geom["type"] == "Polygon"
    assert len(geom["coordinates"]) == 2  # exterior + one interior


def test_two_separate_rings_become_a_multipolygon():
    from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson

    a = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
    b = [[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]
    assert rings_to_geojson([a, b])["type"] == "MultiPolygon"


def test_outer_rings_are_ccw_and_holes_clockwise():
    from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson

    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]
    exterior, interior = rings_to_geojson([outer, hole])["coordinates"]
    assert _signed_area(exterior) > 0  # counter-clockwise
    assert _signed_area(interior) < 0  # clockwise


def test_degenerate_rings_are_dropped_not_raised():
    from src.ingest.jurisdictions.ca_on.waterbody import rings_to_geojson

    geom = rings_to_geojson([[[0, 0], [1, 1]], [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]])
    assert geom["type"] == "Polygon"


# ── fetching ──────────────────────────────────────────────────────────────────


def test_fetch_near_parses_name_area_date_and_geometry(cache_dir):
    from src.ingest.jurisdictions.ca_on.waterbody import fetch_waterbodies_near

    with patch(_HTTP, side_effect=_fake_get(_fixture())), patch(_CACHE, cache_dir):
        bodies = fetch_waterbodies_near(-79.8, 43.3, 5000)

    assert [b.ogf_id for b in bodies] == [321509934, 124215626, 124212064]
    ontario = bodies[0]
    assert ontario.name == "Lake Ontario"
    assert ontario.raw_name == "Lake Ontario (lac Ontario)"
    assert ontario.waterbody_type == "Lake"
    assert ontario.area_m2 == 2583000000.0
    assert ontario.effective_datetime == "2020-10-07"
    assert ontario.geometry["type"] == "Polygon"
    assert bodies[2].name is None  # unnamed stays None, never "Unnamed"


def test_fetch_by_name_uses_a_like_and_escapes_wildcards(cache_dir):
    from src.ingest.jurisdictions.ca_on.waterbody import fetch_waterbodies_by_name

    captured: dict = {}
    with (
        patch(_HTTP, side_effect=_capturing_get({"features": []}, captured)),
        patch(_CACHE, cache_dir),
    ):
        fetch_waterbodies_by_name("50% Lake_name")

    where = captured["where"]
    assert "LIKE" in where
    assert "\\%" in where and "\\_" in where


def test_a_malformed_feature_is_skipped(cache_dir):
    from src.ingest.jurisdictions.ca_on.waterbody import fetch_waterbodies_near

    bad = {"features": [{"attributes": {}, "geometry": {"rings": []}}]}
    with patch(_HTTP, side_effect=_fake_get(bad)), patch(_CACHE, cache_dir):
        assert fetch_waterbodies_near(-79.8, 43.3, 5000) == []


def test_fetch_near_asks_for_a_metre_radius_and_can_skip_geometry(cache_dir):
    from src.ingest.jurisdictions.ca_on.waterbody import fetch_waterbodies_near

    sink: dict = {}
    with patch(_HTTP, side_effect=_capturing_get({"features": []}, sink)), patch(_CACHE, cache_dir):
        fetch_waterbodies_near(-79.8, 43.3, 5000, with_geometry=False)

    assert sink["returnGeometry"] == "false"
    assert sink["distance"] == "5000"
    assert sink["units"] == "esriSRUnit_Meter"
    assert sink["geometryType"] == "esriGeometryPoint"
