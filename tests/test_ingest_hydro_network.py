"""Tests for OHN hydro network ingest module."""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_TEST_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "test_tmp"
_HYDRO_HTTPX = "src.ingest.jurisdictions.ca_on.hydro_network.httpx.get"
_HYDRO_CACHE = "src.ingest.jurisdictions.ca_on.hydro_network._CACHE_DIR"


@pytest.fixture
def cache_dir():
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)
    _TEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield _TEST_CACHE_DIR
    shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def _server(data: dict, count: int | None = None):
    """Fake LIO endpoint: answers count queries and paged feature queries.

    The adapter asks for the bbox's feature count before paging, and judges
    completeness against it, so a fake has to answer both the way the real
    service does. count defaults to the number of features served.
    """
    features = data.get("features", [])
    n = len(features) if count is None else count

    def respond(*args, **kwargs):
        params = kwargs.get("params", {})
        if params.get("returnCountOnly") == "true":
            return _mock_response({"count": n})
        if params.get("resultOffset", 0):
            return _mock_response({"features": []})
        return _mock_response(data)

    return respond


# ── watercourse fetching ──────────────────────────────────────────────────────


def test_fetch_watercourses_returns_all_segments(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    assert len(segments) == 4


def test_named_segment_parsed_correctly(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    named = [s for s in segments if s.name == "Bronte Creek"]
    assert len(named) == 2
    for s in named:
        assert s.flow_verified is True
        assert s.permanency == "Permanent"
        assert s.length_m == 2500.0
        assert s.jurisdiction == "CA-ON"


def test_unnamed_segment_has_none_name(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    unnamed = [s for s in segments if s.name is None]
    assert len(unnamed) == 2


def test_unverified_flow_segment(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    unverified = [s for s in segments if not s.flow_verified]
    assert len(unverified) == 1
    assert unverified[0].ogf_id == 10004


def test_start_end_nodes_rounded_to_5_decimal_places(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    # Seg1: start=(-79.5, 43.5), end=(-79.48, 43.51)
    seg1 = next(s for s in segments if s.ogf_id == 10001)
    assert seg1.start_node == "-79.5,43.5"
    assert seg1.end_node == "-79.48,43.51"


def test_geom_wkt_is_linestring(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    for seg in segments:
        assert seg.geom_wkt.startswith("LINESTRING")


def test_empty_response_returns_empty_list(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    empty = {"features": [], "exceededTransferLimit": False}
    with patch(_HYDRO_HTTPX, side_effect=_server(empty)), patch(_HYDRO_CACHE, cache_dir):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    assert segments == []


# ── barrier fetching ──────────────────────────────────────────────────────────


def test_fetch_barriers_returns_both_barriers(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_barriers

    fixture = _load_fixture("ohn_barriers_response.json")
    with patch(_HYDRO_HTTPX, side_effect=_server(fixture)), patch(_HYDRO_CACHE, cache_dir):
        barriers = fetch_barriers(43.5, -79.48, radius_km=10)

    assert len(barriers) == 2


def test_falls_barrier_parsed(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_barriers

    fixture = _load_fixture("ohn_barriers_response.json")
    with patch(_HYDRO_HTTPX, side_effect=_server(fixture)), patch(_HYDRO_CACHE, cache_dir):
        barriers = fetch_barriers(43.5, -79.48, radius_km=10)

    falls = next(b for b in barriers if b.barrier_type == "Falls")
    assert falls.ogf_id == 20001
    assert falls.geom_wkt.startswith("POINT")


def test_sea_lamprey_barrier_parsed(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_barriers

    fixture = _load_fixture("ohn_barriers_response.json")
    with patch(_HYDRO_HTTPX, side_effect=_server(fixture)), patch(_HYDRO_CACHE, cache_dir):
        barriers = fetch_barriers(43.5, -79.48, radius_km=10)

    slb = next(b for b in barriers if b.barrier_type == "Sea Lamprey Barrier")
    assert slb.ogf_id == 20002


def test_barrier_snaps_to_nearest_segment(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_barriers, fetch_watercourses

    wc_fixture = _load_fixture("ohn_watercourse_response.json")
    b_fixture = _load_fixture("ohn_barriers_response.json")

    with (
        patch(_HYDRO_HTTPX, side_effect=_server(wc_fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    with (
        patch(_HYDRO_HTTPX, side_effect=_server(b_fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        barriers = fetch_barriers(43.5, -79.48, radius_km=10, segments=segments)

    # Falls barrier at (-79.475, 43.5075) lies exactly on Seg3 (OGF_ID 10003)
    falls = next(b for b in barriers if b.barrier_type == "Falls")
    assert falls.nearest_segment_ogf_id == 10003
    assert falls.snap_distance_m is not None
    assert falls.snap_distance_m < 10.0  # essentially on the line

    # Sea Lamprey Barrier at (-79.47, 43.515) lies on Seg2 (OGF_ID 10002)
    slb = next(b for b in barriers if b.barrier_type == "Sea Lamprey Barrier")
    assert slb.nearest_segment_ogf_id == 10002


# ── geometry simplification ───────────────────────────────────────────────────


def test_segment_not_simplified_within_75km(cache_dir):
    """Segment close to home keeps LINESTRING WKT."""
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        # Home at 43.5, -79.48 — fixture segments are <2km away
        segments = fetch_watercourses(43.5, -79.48, radius_km=10)

    for seg in segments:
        assert seg.geom_wkt.startswith("LINESTRING"), (
            f"Segment {seg.ogf_id} should be LINESTRING when <75km from home"
        )


def test_segment_simplified_beyond_75km(cache_dir):
    """Segment far from home is stored as POINT (centroid) WKT."""
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    # Grid tiling makes many HTTP calls (one per sub-tile); use return_value so
    # any number of calls succeeds. OGF_IDs deduplicate across tiles, giving 4 segments.
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        # Home in eastern Ontario (~450km from fixture segments near Toronto)
        segments = fetch_watercourses(46.5, -76.0, radius_km=500)

    assert len(segments) > 0
    for seg in segments:
        assert seg.geom_wkt.startswith("POINT"), (
            f"Segment {seg.ogf_id} should be simplified to POINT when >75km from home"
        )
        # start_node and end_node must still be set (topology preserved)
        assert seg.start_node
        assert seg.end_node


# ── tiled pagination ──────────────────────────────────────────────────────────


def test_short_tile_is_split_until_it_matches_the_server_count(cache_dir):
    """A page shorter than the server's count is a thinned page, not the last one.

    The LIO service returned 4,381 of 8,344 features for a dense 0.5° tile with
    no exceededTransferLimit flag. Judging completeness by page length read that
    as a complete tile; the count query is what exposes the shortfall.
    """
    from src.ingest.jurisdictions.ca_on.hydro_network import _fetch_tile

    fixture = _load_fixture("ohn_watercourse_response.json")
    feats = fixture["features"]  # 4 features; the server claims 8 exist
    calls: list[dict] = []
    quadrant_ids: dict[str, int] = {}

    def thinning_server(*args, **kwargs):
        params = kwargs.get("params", {})
        calls.append(params)
        bbox = params["geometry"]
        whole_tile = bbox == "0.00000,0.00000,1.00000,1.00000"
        if params.get("returnCountOnly") == "true":
            return _mock_response({"count": 8 if whole_tile else 2})
        if whole_tile:
            return _mock_response({"features": feats})  # thinned: 4 of 8
        base = 100 + 10 * quadrant_ids.setdefault(bbox, len(quadrant_ids))
        return _mock_response(
            {"features": [_relabel(f, base + i) for i, f in enumerate(feats[:2])]}
        )

    with patch(_HYDRO_HTTPX, side_effect=thinning_server), patch(_HYDRO_CACHE, cache_dir):
        result = _fetch_tile("http://lio.test/query", {}, 0.0, 0.0, 1.0, 1.0)

    assert len(result) == 8, "four quadrants x two features each"
    assert sum(1 for c in calls if c.get("returnCountOnly") == "true") == 5


def test_complete_tile_is_not_split(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import _fetch_tile

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)) as mock_get,
        patch(_HYDRO_CACHE, cache_dir),
    ):
        result = _fetch_tile("http://lio.test/query", {}, 0.0, 0.0, 1.0, 1.0)

    assert len(result) == len(fixture["features"])
    assert mock_get.call_count == 2  # one count, one page


def test_empty_bbox_skips_the_feature_query(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import _fetch_tile

    with (
        patch(_HYDRO_HTTPX, side_effect=_server({"features": []})) as mock_get,
        patch(_HYDRO_CACHE, cache_dir),
    ):
        assert _fetch_tile("http://lio.test/query", {}, 0.0, 0.0, 1.0, 1.0) == []

    assert mock_get.call_count == 1


def _relabel(feat: dict, ogf_id: int) -> dict:
    return {**feat, "attributes": {**feat["attributes"], "OGF_ID": ogf_id}}


# ── caching ───────────────────────────────────────────────────────────────────


def test_cache_hit_skips_http(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    # One count query plus one page (4 features << _PAGE_SIZE) on the first
    # fetch. The second fetch is a cache hit for both — no further HTTP calls.
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)) as mock_get,
        patch(_HYDRO_CACHE, cache_dir),
    ):
        fetch_watercourses(43.5, -79.48, radius_km=10)
        fetch_watercourses(43.5, -79.48, radius_km=10)

    assert mock_get.call_count == 2


def test_cache_miss_writes_file(cache_dir):
    from src.ingest.jurisdictions.ca_on.hydro_network import fetch_watercourses

    fixture = _load_fixture("ohn_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_server(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        fetch_watercourses(43.5, -79.48, radius_km=10)

    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 2  # the count and the page are each cached


# --- A segment missing from the snap index is invisible downstream ---
#
# _build_snap_index used to `continue` past unparseable geometries silently.
# A segment absent from the index is invisible to every snap that follows, so
# barriers and observations near it attach to a different reach or to nothing.
# That renders in describe() as "no barriers here" — a claim about the water,
# produced by a parse failure.


def _segment(ogf_id: int, wkt: str):
    from src.models.hydrology import StreamSegment

    return StreamSegment(
        ogf_id=ogf_id,
        watercourse_type="Stream",
        flow_verified=True,
        permanency="Permanent",
        length_m=100.0,
        geom_wkt=wkt,
        start_node="-79.70000,43.40000",
        end_node="-79.70100,43.40100",
    )


_GOOD = "LINESTRING(-79.7 43.4, -79.701 43.401)"
_BAD = "NOT WKT AT ALL"


def test_all_geometries_unparseable_is_loud_not_silent(caplog):
    from src.ingest.jurisdictions.ca_on.hydro_network import _build_snap_index

    with caplog.at_level("WARNING"):
        index = _build_snap_index([_segment(1, _BAD), _segment(2, _BAD)])

    assert index is None
    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "all 2 segment geometries failed to parse" in warnings
    assert "read as 'nothing here'" in warnings


def test_a_material_share_of_bad_geometries_warns(caplog):
    from src.ingest.jurisdictions.ca_on.hydro_network import _build_snap_index

    segments = [_segment(i, _GOOD) for i in range(50)] + [_segment(99, _BAD)]
    with caplog.at_level("WARNING"):
        index = _build_snap_index(segments)

    assert index is not None
    _tree, ogf_ids = index
    assert 99 not in ogf_ids, "the bad geometry is genuinely excluded"
    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "1 of 51 segment geometries failed to parse" in warnings
    assert "[99]" in warnings


def test_a_clean_index_does_not_warn(caplog):
    from src.ingest.jurisdictions.ca_on.hydro_network import _build_snap_index

    with caplog.at_level("WARNING"):
        index = _build_snap_index([_segment(1, _GOOD), _segment(2, _GOOD)])

    assert index is not None
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
