"""Tests for Manitoba stocking ingest — no live downloads.

The fixture is a small ArcGIS FeatureServer response (layer 0, "Manitoba
Waterbody Stocking Records") with a coordinate-bearing row, a row whose
coordinates are the 0/0 sentinel, and a row with a blank species.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_mb import stocking
from src.services.mb_ingest import ingest_mb_stocking
from src.storage.database import get_db

FIXTURE = Path(__file__).parent / "fixtures" / "mb_stocking_response.json"
_HTTPX = "src.ingest.jurisdictions.ca_mb.stocking.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_mb.stocking._CACHE_DIR"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def _lat_lng() -> tuple[float, float]:
    return 49.9, -97.14


def test_fetch_returns_records_and_skips_blank_species(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        rows = stocking.fetch_stocking_records(*_lat_lng(), radius_km=50)

    assert len(rows) == 2
    assert {r["record_id"] for r in rows} == {"MB_STOCK_1", "MB_STOCK_2"}


def test_record_fields_mapped(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        rows = stocking.fetch_stocking_records(*_lat_lng(), radius_km=50)

    row = next(r for r in rows if r["record_id"] == "MB_STOCK_1")
    assert row["waterbody_name"] == "Kingfisher Lake"
    assert row["waterbody_code"] == "W1"
    assert row["county"] == "Division 1"
    assert row["species"] == "Rainbow Trout"
    assert row["life_stage"] == "Fingerling"
    assert row["quantity"] == 5000
    assert row["year"] == 2023
    assert row["month"] == 1
    assert row["stocked_at"] == "2023-01-01T00:00:00"
    assert row["lat"] == 49.9
    assert row["lng"] == -97.14
    assert row["jurisdiction"] == "CA-MB"


def test_zero_coordinates_become_null_and_warn(tmp_path, caplog):
    with (
        patch(_HTTPX, return_value=_mock_response(_load())),
        patch(_CACHE, tmp_path),
        caplog.at_level(logging.WARNING, logger=stocking.__name__),
    ):
        rows = stocking.fetch_stocking_records(*_lat_lng(), radius_km=50)

    row = next(r for r in rows if r["record_id"] == "MB_STOCK_2")
    assert row["lat"] is None and row["lng"] is None
    assert "no usable coordinates" in " ".join(r.getMessage() for r in caplog.records)


def test_cache_hit_skips_http(tmp_path):
    with patch(_HTTPX, return_value=_mock_response(_load())) as mock_get, patch(_CACHE, tmp_path):
        stocking.fetch_stocking_records(*_lat_lng(), radius_km=50)
        stocking.fetch_stocking_records(*_lat_lng(), radius_km=50)

    assert mock_get.call_count == 1


def test_ingest_stores_records_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, return_value=_mock_response(_load())), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.mb_ingest.get_db", lambda: get_db(db_path))
        n = ingest_mb_stocking(*_lat_lng(), radius_km=50)
        assert n == 2
        db = get_db(db_path)
        assert db["stocking_records"].count == 2
        assert {r["jurisdiction"] for r in db["stocking_records"].rows} == {"CA-MB"}

        assert ingest_mb_stocking(*_lat_lng(), radius_km=50) == 2
        assert db["stocking_records"].count == 2
