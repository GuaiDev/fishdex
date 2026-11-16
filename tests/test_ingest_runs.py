"""Durable ingest ledger behavior."""

import logging

from src.services.admin_dashboard import build_dashboard
from src.services.ingest_runs import ingest_run
from src.storage.database import get_db


def test_silent_empty_run_is_flagged_and_visible_in_admin_dashboard(tmp_path, caplog):
    db = get_db(tmp_path / "test.db")
    with caplog.at_level(logging.WARNING, logger="src.services.ingest_runs"):
        with ingest_run("Overpass", "CA-ON", "Oakville", db=db) as run:
            # A successful HTTP response that yielded no records across a batch.
            run.record(fetched=20, stored=0, skipped=0, empty=18)

    row = db.execute(
        "SELECT fetched, stored, skipped, empty_share, status FROM ingest_runs"
    ).fetchone()
    assert row == (20, 0, 0, 0.9, "warning")
    assert "returned empty answers for 18/20" in caplog.text
    dashboard = build_dashboard(db)
    assert dashboard["ingest_status"]["runs"][0]["source"] == "Overpass"
    assert dashboard["ingest_status"]["runs"][0]["empty_share"] == 0.9


def test_ingest_run_records_failure_and_error(tmp_path):
    db = get_db(tmp_path / "test.db")
    try:
        with ingest_run("test source", db=db) as run:
            run.record(fetched=2, skipped=2)
            raise RuntimeError("upstream unavailable")
    except RuntimeError:
        pass

    row = db.execute("SELECT status, error, stored, skipped FROM ingest_runs").fetchone()
    assert row == ("failed", "RuntimeError: upstream unavailable", 0, 2)


def test_wsc_counts_reach_area_and_cli_ledgers(db, monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime

    from src.cli.main import _run_source
    from src.models.ingest_area import AreaIngestResult, IngestSource
    from src.services import area_ingest, ingest_runs, stream_gauge

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(stream_gauge, "get_db", lambda: db)
    stations = [
        {"STATION_NUMBER": str(i), "LATITUDE": 51.0, "LONGITUDE": -114.0} for i in range(20)
    ]
    stations.append({})
    monkeypatch.setattr(stream_gauge._wsc, "fetch_nearby_stations", lambda *a: stations)

    def reading(station_id, *args):
        if int(station_id) >= 2:
            return None
        return SimpleNamespace(
            station_id=station_id,
            station_name="Gauge",
            river_name="Bow",
            lat=51.0,
            lng=-114.0,
            jurisdiction="CA-AB",
            water_level_m=1.0,
            discharge_cms=1.0,
            level_trend=None,
            discharge_trend=None,
            level_grade=None,
            reading_datetime=datetime.now(),
            fetched_at=datetime.now(),
        )

    monkeypatch.setattr(stream_gauge._wsc, "fetch_station_reading", reading)
    result = AreaIngestResult(label="Calgary", source=IngestSource.GLOBAL, jurisdiction="CA-AB")
    area_ingest._attempt(result, "WSC", lambda: stream_gauge.fetch_and_store(51, -114))
    _run_source([], "WSC", "fetching", stream_gauge.fetch_and_store, 51, -114, jurisdiction="CA-AB")
    rows = list(
        db.execute(
            "SELECT jurisdiction, fetched, stored, skipped, empty_count, status FROM ingest_runs"
        )
    )
    assert rows == [("CA-AB", 21, 2, 1, 18, "warning")] * 2


def test_configured_jurisdiction_reaches_global_and_tidal_ledgers(db, monkeypatch):
    import importlib

    from src.models.ingest_area import IngestArea
    from src.services import area_ingest, gbif, ingest_runs, observations, osm, stream_gauge

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(area_ingest, "get_db", lambda: db)
    for module in (gbif, observations, stream_gauge):
        monkeypatch.setattr(module, "fetch_and_store", lambda *a, **k: 0)
    monkeypatch.setattr(osm, "fetch_and_store", lambda *a, **k: (0, 0))
    monkeypatch.setattr(area_ingest, "_check_sdm_retrain", lambda *a: None)
    tidal = importlib.import_module("src.ingest.jurisdictions.ca_national.tidal")
    monkeypatch.setattr(tidal, "fetch_tidal_readings", lambda *a: [])
    area = IngestArea(
        label="Coast",
        jurisdiction="CA-NS",
        lat=45,
        lng=-65,
        radius_km=50,
        sources=["global", "tidal"],
    )
    for source in area.sources:
        area_ingest.run_area(area, source)
    rows = list(db.execute("SELECT jurisdiction, area FROM ingest_runs"))
    assert rows == [("CA-NS", "Coast")] * 5
