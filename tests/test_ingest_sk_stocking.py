"""Tests for Saskatchewan stocking ingest — no live downloads.

The source is a geometry-free table (Fish Stocking History, FeatureServer/4)
joined to the Species Info points (FeatureServer/1) via NRCAN→LOCATIONID. The
fixture covers a blank-species row (dropped) and a row whose waterbody code has
no resolvable point (kept with NULL coordinates). STOCKING_DATE can be a
negative epoch for pre-1970 stockings.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingest.jurisdictions.ca_sk import stocking
from src.services.sk_ingest import ingest_sk_stocking
from src.storage.database import get_db

FIXTURES = Path(__file__).parent / "fixtures"
_POINTS = json.loads((FIXTURES / "sk_fish_atlas_points.json").read_text())
_STOCKING = json.loads((FIXTURES / "sk_stocking_response.json").read_text())
_HTTPX = "src.ingest.jurisdictions.ca_sk.stocking.httpx.get"
_CACHE = "src.ingest.jurisdictions.ca_sk.stocking._CACHE_DIR"


def _mock_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


def _server(url, **kwargs):
    if "/1/query" in url:
        return _mock_response(_POINTS)
    return _mock_response(_STOCKING)


def test_fetch_returns_rows_and_skips_blank_species(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        rows = stocking.fetch_stocking_records(52.13, -106.67, radius_km=50)

    assert len(rows) == 3
    assert {r["record_id"] for r in rows} == {"SK_STOCK_7001", "SK_STOCK_7002", "SK_STOCK_7004"}


def test_row_fields_mapped(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        rows = stocking.fetch_stocking_records(52.13, -106.67, radius_km=50)

    row = next(r for r in rows if r["record_id"] == "SK_STOCK_7001")
    assert row["waterbody_name"] == "Lake A"
    assert row["waterbody_code"] == "ABC01"
    assert row["species"] == "Rainbow Trout"
    assert row["life_stage"] == "Fingerlings"
    assert row["quantity"] == 5000
    assert row["year"] == 2021
    assert row["month"] == 1
    assert row["lat"] == 52.13
    assert row["lng"] == -106.67
    assert row["jurisdiction"] == "CA-SK"


def test_negative_epoch_resolves_to_pre_1970_year(tmp_path):
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        rows = stocking.fetch_stocking_records(52.13, -106.67, radius_km=50)

    row = next(r for r in rows if r["record_id"] == "SK_STOCK_7002")
    assert row["year"] == 1965
    assert row["month"] == 1


def test_unresolved_waterbody_kept_with_null_coords_and_warns(tmp_path, caplog):
    with (
        patch(_HTTPX, side_effect=_server),
        patch(_CACHE, tmp_path),
        caplog.at_level(logging.WARNING, logger=stocking.__name__),
    ):
        rows = stocking.fetch_stocking_records(52.13, -106.67, radius_km=50)

    row = next(r for r in rows if r["record_id"] == "SK_STOCK_7004")
    assert row["lat"] is None and row["lng"] is None
    assert row["waterbody_name"] == "Unknown Lake"
    assert "no resolvable coordinates" in " ".join(r.getMessage() for r in caplog.records)


def test_service_stores_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    with patch(_HTTPX, side_effect=_server), patch(_CACHE, tmp_path):
        monkeypatch.setattr("src.services.sk_ingest.get_db", lambda: get_db(db_path))
        n = ingest_sk_stocking(52.13, -106.67, radius_km=50)

    assert n == 3
    rows = list(get_db(db_path)["stocking_records"].rows)
    assert {r["jurisdiction"] for r in rows} == {"CA-SK"}
