"""Tests for FWMIS (Alberta) hydro network ingest module."""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_TEST_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "test_tmp"
_HYDRO_HTTPX = "src.ingest.jurisdictions.ca_ab.hydro_network.httpx.get"
_HYDRO_CACHE = "src.ingest.jurisdictions.ca_ab.hydro_network._CACHE_DIR"


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


def _paged_side_effect(first_data: dict) -> list:
    """First HTTP call returns first_data; second returns empty to stop pagination."""
    return [
        _mock_response(first_data),
        _mock_response({"features": [], "exceededTransferLimit": False}),
    ]


# ── watercourse fetching ──────────────────────────────────────────────────────


def test_fetch_watercourses_returns_all_segments(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    assert len(segments) == 4


def test_named_segment_uses_common_name(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    named = [s for s in segments if s.name == "Bow River"]
    assert len(named) == 1
    for s in named:
        assert s.stream_order == 2
        assert s.length_m == 5000.0
        assert s.jurisdiction == "CA-AB"
        assert s.segment_source == "FWMIS"
        assert s.watercourse_type == "Stream"


def test_official_name_used_when_common_null(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    # FWMIS ships official names in upper case; preserved as-is
    named = [s for s in segments if s.name == "ELBOW RIVER"]
    assert len(named) == 1
    assert named[0].stream_order == 3


def test_unnamed_segment_has_none_name(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    unnamed = [s for s in segments if s.name is None]
    assert len(unnamed) == 2


def test_start_end_nodes_rounded_to_5_decimal_places(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    seg1 = next(s for s in segments if s.ogf_id == 10001)
    assert seg1.start_node == "-114.2,51.0"
    assert seg1.end_node == "-114.18,51.02"


def test_geom_wkt_is_linestring(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    for seg in segments:
        assert seg.geom_wkt.startswith("LINESTRING")


def test_empty_response_returns_empty_list(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    empty = {"features": [], "exceededTransferLimit": False}
    with patch(_HYDRO_HTTPX, return_value=_mock_response(empty)), patch(_HYDRO_CACHE, cache_dir):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    assert segments == []


# ── pagination ────────────────────────────────────────────────────────────────


def test_pagination_continues_while_exceeded_transfer_limit(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    page1 = _load_fixture("fwmis_watercourse_response.json")
    page1["exceededTransferLimit"] = True
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(page1)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    assert len(segments) == 4  # page 1 (4 features) + page 2 (empty)


# ── tiled pagination ──────────────────────────────────────────────────────────


def test_tiling_triggered_on_exact_page_size(cache_dir):
    """When a tile returns exactly _PAGE_SIZE records, bbox is split into quadrants."""
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    empty_response = _mock_response({"features": [], "exceededTransferLimit": False})

    call_log: list[int] = []

    def counting_side_effect(*args, **kwargs):
        call_log.append(1)
        if len(call_log) == 1:
            return _mock_response(fixture)
        return empty_response

    with (
        patch(_HYDRO_HTTPX, side_effect=counting_side_effect),
        patch(_HYDRO_CACHE, cache_dir),
        patch("src.ingest.jurisdictions.ca_ab.hydro_network._PAGE_SIZE", 4),
    ):
        fetch_watercourses(51.05, -114.07, radius_km=10)

    # page 0 = 4 features (= capped page size) + 4 quadrant calls = 5 calls
    assert len(call_log) >= 5, (
        f"Expected ≥5 HTTP calls when tiling is triggered, got {len(call_log)}"
    )


# ── geometry simplification ───────────────────────────────────────────────────


def test_segment_not_simplified_within_75km(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, side_effect=_paged_side_effect(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        # Home at 51.05, -114.07 — fixture segments are <60km away
        segments = fetch_watercourses(51.05, -114.07, radius_km=10)

    assert len(segments) == 4
    for seg in segments:
        assert seg.geom_wkt.startswith("LINESTRING"), (
            f"Segment {seg.ogf_id} should be LINESTRING when <75km from home"
        )


def test_segment_simplified_beyond_75km(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    # Tiling makes many HTTP calls (one per sub-tile); use return_value so any
    # number of calls succeeds. OBJECTIDs deduplicate across tiles → 4 segments.
    with (
        patch(_HYDRO_HTTPX, return_value=_mock_response(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        # Home ~1700km away from fixture segments near Calgary
        segments = fetch_watercourses(46.5, -76.0, radius_km=500)

    assert len(segments) > 0
    for seg in segments:
        assert seg.geom_wkt.startswith("POINT"), (
            f"Segment {seg.ogf_id} should be simplified to POINT when >75km from home"
        )
        # start_node and end_node must still be set (topology preserved)
        assert seg.start_node
        assert seg.end_node


# ── caching ───────────────────────────────────────────────────────────────────


def test_cache_hit_skips_http(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    # 10km radius → one sub-tile → one HTTP call for the first fetch.
    with (
        patch(_HYDRO_HTTPX, return_value=_mock_response(fixture)) as mock_get,
        patch(_HYDRO_CACHE, cache_dir),
    ):
        fetch_watercourses(51.05, -114.07, radius_km=10)
        fetch_watercourses(51.05, -114.07, radius_km=10)

    assert mock_get.call_count == 1


def test_cache_miss_writes_file(cache_dir):
    from src.ingest.jurisdictions.ca_ab.hydro_network import fetch_watercourses

    fixture = _load_fixture("fwmis_watercourse_response.json")
    with (
        patch(_HYDRO_HTTPX, return_value=_mock_response(fixture)),
        patch(_HYDRO_CACHE, cache_dir),
    ):
        fetch_watercourses(51.05, -114.07, radius_km=10)

    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1