"""Durable ingest ledger behavior."""

import logging

import pytest

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


def test_pwqmn_resource_counts_reach_cli_ledger(db, monkeypatch):
    from src.cli.main import _run_source
    from src.ingest.jurisdictions.ca_on import water_quality as adapter
    from src.services import ingest_runs, water_quality

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(water_quality, "get_db", lambda: db)
    monkeypatch.setattr(adapter, "load_stations", lambda: {})
    monkeypatch.setattr(
        adapter,
        "_get_field_data_resources",
        lambda: [{"name": str(i), "url": str(i)} for i in range(21)],
    )
    monkeypatch.setattr(
        adapter, "download_field_data", lambda name, url: None if name == "20" else int(name)
    )
    monkeypatch.setattr(
        adapter, "parse_field_data", lambda path, stations: [path] if path < 2 else []
    )
    written = []
    monkeypatch.setattr(
        water_quality, "upsert_water_quality_readings", lambda db, records: written.extend(records)
    )
    assert _run_source([], "PWQMN", "fetching", water_quality.ingest_water_quality_data) == 2
    assert written == [0, 1]
    assert db.execute(
        "SELECT fetched, stored, skipped, empty_count, status FROM ingest_runs"
    ).fetchone() == (21, 2, 1, 18, "warning")


@pytest.mark.parametrize("wrapper", ["area", "cli"])
@pytest.mark.parametrize("failed_dataset", ["water", "access"])
def test_osm_caught_timeouts_fail_ledger_and_continue(db, monkeypatch, wrapper, failed_dataset):
    import httpx

    from src.cli.main import _run_source
    from src.models.ingest_area import AreaIngestResult, IngestSource
    from src.services import area_ingest, ingest_runs, osm

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(osm, "get_db", lambda: db)

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("Overpass unavailable")

    monkeypatch.setattr(
        osm._osm,
        "fetch_water_features",
        timeout if failed_dataset == "water" else lambda *a, **k: [],
    )
    monkeypatch.setattr(osm._osm, "fetch_access_points", timeout)
    with pytest.warns(UserWarning, match="OSM ingest failed"):
        if wrapper == "area":
            result = AreaIngestResult(label="Coast", source=IngestSource.GLOBAL)
            area_ingest._attempt(
                result, "OSM", lambda: {"OSM water features": osm.fetch_and_store(45, -65)[0]}
            )
            assert result.stored == {"OSM water features": 0}
        else:
            assert _run_source([], "OSM", "fetching", osm.fetch_and_store, 45, -65) == (0, 0)
    assert db.execute("SELECT status, error FROM ingest_runs").fetchone() == (
        "failed",
        "ReadTimeout: Overpass unavailable",
    )
    with ingest_run("next source", db=db):
        pass
    assert db.execute("SELECT status FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone() == (
        "success",
    )


@pytest.mark.parametrize("failure", [None, "station", "discovery"])
def test_tidal_station_diagnostics_reach_ledger(db, monkeypatch, failure):
    import httpx

    from src.ingest.jurisdictions.ca_national import tidal
    from src.services import area_ingest, ingest_runs

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(area_ingest, "get_db", lambda: db)
    examined = []

    def response(url, params, ttl):
        if url == tidal._STATIONS_URL:
            if failure == "discovery":
                raise httpx.ReadTimeout("station discovery failed")
            return [{"id": str(i), "latitude": 45, "longitude": -65} for i in range(20)]
        station_id = url.split("/")[-2]
        examined.append(station_id)
        if failure == "station" and station_id == "0":
            raise httpx.ReadTimeout("station predictions failed")
        if int(station_id) < 18:
            return []
        return [{"eventDate": "2026-11-16T12:00:00Z", "value": 1.2, "type": "high"}]

    monkeypatch.setattr(tidal, "_cached_get", response)
    result = area_ingest.run_tidal_ingest(45, -65, 100, "Coast", jurisdiction="CA-NS")
    row = db.execute(
        "SELECT fetched, stored, skipped, empty_count, status, error FROM ingest_runs"
    ).fetchone()
    if failure == "discovery":
        assert row == (1, 0, 1, 0, "failed", "ReadTimeout: station discovery failed")
        assert examined == []
        assert result.stored == {"CHS tide predictions": 0}
    else:
        assert examined == [str(i) for i in range(20)]
        assert result.stored == {"CHS tide predictions": 2}
        assert db.execute("SELECT COUNT(*) FROM tidal_readings").fetchone() == (2,)
        if failure == "station":
            assert row == (20, 2, 1, 17, "failed", "ReadTimeout: station predictions failed")
        else:
            assert row == (20, 2, 0, 18, "warning", None)


@pytest.mark.parametrize("status", [404, 500])
def test_pwqmn_http_download_failures_reach_ledger(db, monkeypatch, tmp_path, status):
    from contextlib import contextmanager

    import httpx

    from src.cli.main import _run_source
    from src.ingest.jurisdictions.ca_on import water_quality as adapter
    from src.services import ingest_runs, water_quality

    monkeypatch.setattr(ingest_runs, "get_db", lambda: db)
    monkeypatch.setattr(water_quality, "get_db", lambda: db)
    monkeypatch.setattr(adapter, "_RAW_DIR", tmp_path)
    monkeypatch.setattr(adapter, "load_stations", lambda: {})
    urls = ["https://example.com/first.csv", "https://example.com/second.csv"]
    monkeypatch.setattr(
        adapter,
        "_get_field_data_resources",
        lambda: [{"name": str(i), "url": url} for i, url in enumerate(urls)],
    )
    attempted = []

    @contextmanager
    def stream(method, url, **kwargs):
        attempted.append(url)
        yield httpx.Response(status, request=httpx.Request(method, url))

    monkeypatch.setattr(adapter.httpx, "stream", stream)
    assert _run_source([], "PWQMN", "fetching", water_quality.ingest_water_quality_data) == 0
    assert attempted == urls
    row = db.execute(
        "SELECT fetched, stored, skipped, empty_count, status, error FROM ingest_runs"
    ).fetchone()
    assert row[:5] == (2, 0, 2, 0, "failed")
    assert row[5].startswith("HTTPStatusError:")
    assert str(status) in row[5]
    assert urls[-1] in row[5]
