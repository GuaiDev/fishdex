"""Tests for Manitoba WaLLAS stream-network ingest — no live downloads.

The fixture is a small GeoJSON response from WaLLAS layer 18
("Watercourses (NTS 1:20,000)"): a named permanent stream, an unnamed
indefinite one, and a MultiLineString.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_mb import hydro_network
from src.services.mb_ingest import ingest_mb_hydro_network
from src.storage.database import get_db

FIXTURE = Path(__file__).parent / "fixtures" / "mb_wallas_response.json"
_HTTPX = "src.ingest.jurisdictions.ca_mb.hydro_network.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_mb.hydro_network._CACHE_DIR"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def test_fetch_returns_all_segments(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)

    assert len(segments) == 3
    assert {s.ogf_id for s in segments} == {101, 102, 103}


def test_named_permanent_segment_fields(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 101)
    assert seg.name == "Red River"
    assert seg.permanency == "Permanent"
    assert seg.stream_order == 5
    assert seg.jurisdiction == "CA-MB"
    assert seg.segment_source == "WaLLAS"
    assert seg.geom_wkt.startswith("LINESTRING")
    assert seg.length_m > 0
    assert seg.start_node == "-97.14,49.9"


def test_unnamed_indefinite_segment(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 102)
    assert seg.name is None
    assert seg.permanency == "Seasonal"
    assert seg.stream_order is None
    assert seg.flow_classification == "RIVER/STREAM, INDEFINITE"


def test_multilinestring_parsed(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 103)
    assert seg.geom_wkt.startswith("MULTILINESTRING")
    assert seg.name == "Assiniboine River"


def test_cache_hit_skips_http(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())) as mock_get, patch(_CACHE, tmp_path):
        hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)
        hydro_network.fetch_watercourses(49.9, -97.14, radius_km=50)

    assert mock_get.call_count == 1


def test_service_stores_segments(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.mb_ingest.get_db", lambda: get_db(db_path))
        segs, barriers = ingest_mb_hydro_network(49.9, -97.14, radius_km=50)

    assert segs == 3
    assert barriers == 0
    db = get_db(db_path)
    rows = list(db["stream_segments"].rows)
    assert {r["jurisdiction"] for r in rows} == {"CA-MB"}
    assert {r["segment_source"] for r in rows} == {"WaLLAS"}
