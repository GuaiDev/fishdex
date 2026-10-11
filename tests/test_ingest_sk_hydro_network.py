"""Tests for Saskatchewan hydrography ingest — no live downloads.

The fixture is a small GeoJSON response from the SaskGIS Hydrography
MapServer layer 121: a named permanent watercourse, an unnamed intermittent
ditch, and a canal.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_sk import hydro_network
from src.services.sk_ingest import ingest_sk_hydro_network
from src.storage.database import get_db

FIXTURE = Path(__file__).parent / "fixtures" / "sk_hydro_response.json"
_HTTPX = "src.ingest.jurisdictions.ca_sk.hydro_network.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_sk.hydro_network._CACHE_DIR"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def test_fetch_returns_all_segments(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)

    assert len(segments) == 3
    assert {s.ogf_id for s in segments} == {201, 202, 203}


def test_named_permanent_watercourse(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 201)
    assert seg.name == "South Saskatchewan River"
    assert seg.watercourse_type == "Stream"
    assert seg.permanency == "Permanent"
    assert seg.stream_order is None  # not published by the source
    assert seg.jurisdiction == "CA-SK"
    assert seg.segment_source == "SKHydro"


def test_intermittent_ditch_is_unnamed(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 202)
    assert seg.name is None
    assert seg.watercourse_type == "Ditch"
    assert seg.permanency == "Intermittent"


def test_canal_definition_mapped(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        segments = hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)

    seg = next(s for s in segments if s.ogf_id == 203)
    assert seg.watercourse_type == "Canal"


def test_cache_hit_skips_http(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())) as mock_get, patch(_CACHE, tmp_path):
        hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)
        hydro_network.fetch_watercourses(52.13, -106.67, radius_km=50)

    assert mock_get.call_count == 1


def test_service_stores_segments(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.sk_ingest.get_db", lambda: get_db(db_path))
        segs, barriers = ingest_sk_hydro_network(52.13, -106.67, radius_km=50)

    assert segs == 3
    assert barriers == 0
    rows = list(get_db(db_path)["stream_segments"].rows)
    assert {r["jurisdiction"] for r in rows} == {"CA-SK"}
    assert {r["segment_source"] for r in rows} == {"SKHydro"}


def test_service_does_not_overwrite_other_province_with_same_raw_id(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    db = get_db(db_path)
    db["stream_segments"].insert(
        {"ogf_id": 201, "name": "MB creek", "jurisdiction": "CA-MB"}, pk="ogf_id"
    )
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.sk_ingest.get_db", lambda: get_db(db_path))
        ingest_sk_hydro_network(52.13, -106.67, radius_km=50)

    rows = list(get_db(db_path)["stream_segments"].rows)
    other = [r for r in rows if r["jurisdiction"] == "CA-MB"]
    assert len(other) == 1 and other[0]["ogf_id"] == 201 and other[0]["name"] == "MB creek"
    assert len([r for r in rows if r["jurisdiction"] == "CA-SK"]) == 3
