"""Tests for OSM Overpass ingest module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _make_mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    mock.raise_for_status.return_value = None
    return mock


# ── water features ────────────────────────────────────────────────────────────


def test_fetch_water_features_returns_all_three(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    assert len(features) == 3


def test_named_lake_parsed_correctly(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    lake = next(f for f in features if f.name == "Heart Lake")
    assert lake.feature_type == "lake"
    assert lake.osm_id == "node/1234567890"
    assert lake.lat == pytest.approx(43.7220)
    assert lake.lng == pytest.approx(-79.4800)


def test_river_centroid_from_way_geometry(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    river = next(f for f in features if f.name == "Humber River")
    assert river.feature_type == "river"
    assert river.osm_id == "way/2345678901"
    # average of 5 geometry points: lats 43.75/43.748/43.746/43.744/43.742 → 43.746
    assert river.lat == pytest.approx(43.7460, abs=0.0001)
    assert river.lng == pytest.approx(-79.5700, abs=0.0001)


def test_unnamed_pond_included_with_no_name(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    pond = next(f for f in features if f.feature_type == "pond")
    assert pond.name is None
    assert pond.osm_id == "way/3456789012"


def test_area_m2_computed_for_closed_polygon(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    pond = next(f for f in features if f.feature_type == "pond")
    assert pond.area_m2 is not None
    assert pond.area_m2 > 0


def test_area_m2_none_for_river_line(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    river = next(f for f in features if f.name == "Humber River")
    assert river.area_m2 is None


def test_area_m2_none_for_node(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    lake = next(f for f in features if f.name == "Heart Lake")
    assert lake.area_m2 is None


def test_osm_id_format(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    ids = {f.osm_id for f in features}
    assert "node/1234567890" in ids
    assert "way/2345678901" in ids
    assert "way/3456789012" in ids


def test_empty_response_returns_empty_list(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")

    with (
        patch("httpx.post", return_value=_make_mock_response({"elements": []})),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        features = osm.fetch_water_features(43.65, -79.38)

    assert features == []


# ── access points ─────────────────────────────────────────────────────────────


def test_fetch_access_points_returns_all_three(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_access.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        points = osm.fetch_access_points(43.65, -79.38)

    assert len(points) == 3


def test_boat_ramp_mapped_to_boat_launch(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_access.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        points = osm.fetch_access_points(43.65, -79.38)

    ramp = next(p for p in points if p.access_type == "boat_launch")
    assert ramp.name == "Etobicoke Creek Boat Launch"
    assert ramp.osm_id == "node/4567890123"


def test_fishing_spot_mapped_correctly(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_access.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        points = osm.fetch_access_points(43.65, -79.38)

    fishing = next(p for p in points if p.access_type == "fishing_spot")
    assert fishing.name == "Humber River Fishing Access"
    assert fishing.osm_id == "node/5678901234"


def test_park_centroid_from_way_geometry(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_access.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        points = osm.fetch_access_points(43.65, -79.38)

    park = next(p for p in points if p.access_type == "park")
    assert park.name == "Etienne Brule Park"
    assert park.osm_id == "way/6789012345"
    # average of 5 polygon vertices (last == first)
    assert park.lat == pytest.approx(43.6958, abs=0.0001)
    assert park.lng == pytest.approx(-79.4738, abs=0.0001)


# ── caching ───────────────────────────────────────────────────────────────────


def test_cache_hit_skips_http(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)) as mock_post,
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        osm.fetch_water_features(43.65, -79.38)
        osm.fetch_water_features(43.65, -79.38)  # second call → cache hit

    assert mock_post.call_count == 1


def test_cache_miss_writes_file(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_water.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        osm.fetch_water_features(43.65, -79.38)

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1


def test_access_cache_hit_skips_http(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    fixture = _load_fixture("osm_access.json")

    with (
        patch("httpx.post", return_value=_make_mock_response(fixture)) as mock_post,
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        osm.fetch_access_points(43.65, -79.38)
        osm.fetch_access_points(43.65, -79.38)

    assert mock_post.call_count == 1


# ── stream corridor (stretch anchors) ─────────────────────────────────────────

CORRIDOR_LINE = [(-79.6346, 42.9033), (-79.6104, 42.8968), (-79.5776, 42.8482)]


def _corridor(osm, tmp_path, response):
    with (
        patch("httpx.post", return_value=_make_mock_response(response)) as post,
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        return osm.fetch_stream_corridor(CORRIDOR_LINE, "CA-ON"), post


def test_corridor_parses_every_kind_from_a_recorded_response(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    features, _ = _corridor(osm, tmp_path, _load_fixture("osm_corridor_dunnville.json"))

    kinds = {f.kind for f in features}
    assert {"weir", "parking", "boat_launch", "road"} <= kinds
    assert all(f.jurisdiction == "CA-ON" for f in features)
    assert len({f.osm_id for f in features}) == len(features)
    for f in features:
        assert 42.8 < f.lat < 43.0 and -79.7 < f.lng < -79.5


def test_corridor_roads_keep_their_line_and_points_do_not(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    features, _ = _corridor(osm, tmp_path, _load_fixture("osm_corridor_dunnville.json"))

    roads = [f for f in features if f.kind == "road"]
    assert roads and all(f.line and len(f.line) >= 2 for f in roads)
    assert all(f.line is None for f in features if f.kind != "road")


def test_corridor_element_kinds_from_tags():
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    kind = osm._corridor_kind
    assert kind({"waterway": "dam"}) == "dam"
    assert kind({"waterway": "weir"}) == "weir"
    assert kind({"waterway": "lock_gate"}) == "lock"
    assert kind({"amenity": "boat_ramp"}) == "boat_launch"
    assert kind({"leisure": "slipway"}) == "boat_launch"
    assert kind({"leisure": "fishing"}) == "fishing_spot"
    assert kind({"highway": "trailhead"}) == "trail_head"
    assert kind({"amenity": "parking"}) == "parking"
    assert kind({"highway": "layby"}) == "parking"
    assert kind({"highway": "secondary"}) == "road"
    assert kind({"building": "yes"}) is None


def test_corridor_way_uses_its_center_and_a_road_without_geometry_is_skipped():
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    lot = {
        "type": "way",
        "id": 7,
        "center": {"lat": 42.9, "lon": -79.6},
        "tags": {"amenity": "parking", "name": "Lot"},
    }
    parsed = osm._parse_corridor_element(lot, None)
    assert (parsed.osm_id, parsed.name, parsed.lat, parsed.lng) == ("way/7", "Lot", 42.9, -79.6)
    assert parsed.jurisdiction == "CA-ON"  # looked up from the coordinates

    no_geometry = {"type": "way", "id": 8, "tags": {"highway": "secondary"}}
    assert osm._parse_corridor_element(no_geometry, None) is None


def test_corridor_query_follows_the_river_line(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    _, post = _corridor(osm, tmp_path, {"elements": []})

    query = post.call_args.kwargs["data"]["data"]
    assert "42.90330,-79.63460,42.89680,-79.61040,42.84820,-79.57760" in query
    assert "around:100," in query and "around:300," in query and "around:60," in query


def test_corridor_caches_and_an_empty_answer_is_not_an_error(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    first, post = _corridor(osm, tmp_path, {"elements": []})
    assert first == [] and post.call_count == 1
    again, post2 = _corridor(osm, tmp_path, {"elements": [{"surprise": True}]})
    assert again == [] and post2.call_count == 0  # served from cache


def test_corridor_with_no_line_makes_no_request(tmp_path):
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    with patch("httpx.post") as post, patch.object(osm, "_CACHE_DIR", tmp_path):
        assert osm.fetch_stream_corridor([(-79.6, 42.9)]) == []
    post.assert_not_called()


def test_overpass_runtime_error_is_retried_and_never_cached(tmp_path):
    """Overpass reports a timed-out query as HTTP 200 with a remark, and an
    overloaded server as an HTML page. Neither may sit in the cache for 30 days."""
    import importlib

    osm = importlib.import_module("src.ingest.global.osm")
    bad = _make_mock_response({"elements": [], "remark": "runtime error: Query timed out"})
    html = MagicMock()
    html.raise_for_status.return_value = None
    html.json.side_effect = ValueError("not json")
    good = _make_mock_response({"elements": []})

    with (
        patch("httpx.post", side_effect=[bad, html, good]) as post,
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path),
    ):
        assert osm.fetch_stream_corridor(CORRIDOR_LINE) == []
    assert post.call_count == 3
    assert len(list(tmp_path.glob("*.json"))) == 1  # only the good answer

    with (
        patch("httpx.post", return_value=bad),
        patch("time.sleep"),
        patch.object(osm, "_CACHE_DIR", tmp_path / "other"),
        pytest.raises(RuntimeError),
    ):
        osm.fetch_stream_corridor(CORRIDOR_LINE)
    assert not list((tmp_path / "other").glob("*.json"))
