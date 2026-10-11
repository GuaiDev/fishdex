"""Tests for Saskatchewan Long Term Water Chemistry ingest — no live downloads.

The source (WSA via ArcGIS Online) stores most chemistry as text with
"NA"/"ND"/"Trace" sentinels and carries no temperature column. Turbidity is in
Jackson units (JTU ≠ FNU), so turbidity_fnu is deliberately left NULL.
"""

import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_sk import water_quality as wq
from src.services.sk_ingest import ingest_sk_water_quality
from src.storage.database import get_db
from src.storage.water_quality import query_water_quality

FIXTURE = Path(__file__).parent / "fixtures" / "sk_wq_response.json"
_HTTPX = "src.ingest.jurisdictions.ca_sk.water_quality.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_sk.water_quality._CACHE_DIR"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def test_fetch_skips_rows_without_a_date(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        readings = wq.fetch_water_quality_readings(52.13, -106.67, radius_km=50)

    assert len(readings) == 2
    assert {r.record_id for r in readings} == {"SK_WQ_8001", "SK_WQ_8002"}


def test_full_reading_parsed_and_lab_ph_preferred(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        readings = wq.fetch_water_quality_readings(52.13, -106.67, radius_km=50)

    r = next(r for r in readings if r.record_id == "SK_WQ_8001")
    assert r.station_id == "HAXX"
    assert r.station_name == "Lake A"
    assert r.sampled_at == date(1965, 6, 15)
    assert r.do_mgl == 8.4
    assert r.ph == 7.8
    assert r.conductivity_us_cm == 420.0
    assert r.temp_c is None
    assert r.turbidity_fnu is None
    assert r.jurisdiction == "CA-SK"


def test_sentinels_and_field_ph_fallback(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        readings = wq.fetch_water_quality_readings(52.13, -106.67, radius_km=50)

    r = next(r for r in readings if r.record_id == "SK_WQ_8002")
    assert r.do_mgl is None  # "ND"
    assert r.conductivity_us_cm is None  # "Trace"
    assert r.ph == 7.1  # lab "NA", falls back to field pH
    assert r.station_id == "Lake B"  # CGNDB "NA" falls back to the name
    assert r.sampled_at == date(1972, 3, 1)


def test_no_date_warns(tmp_path, caplog):
    with (
        patch(_HTTPX, return_value=_mock_response(_load())),
        patch(_CACHE, tmp_path),
        caplog.at_level(logging.WARNING, logger=wq.__name__),
    ):
        wq.fetch_water_quality_readings(52.13, -106.67, radius_km=50)

    assert "no usable collection year" in " ".join(r.getMessage() for r in caplog.records)


def test_service_stores_readings(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.sk_ingest.get_db", lambda: get_db(db_path))
        n = ingest_sk_water_quality(52.13, -106.67, radius_km=50)

    assert n == 2
    stored = query_water_quality(get_db(db_path), lat=52.13, lng=-106.67, radius_km=50)
    assert {r.jurisdiction for r in stored} == {"CA-SK"}
    assert {r.record_id for r in stored} == {"SK_WQ_8001", "SK_WQ_8002"}
