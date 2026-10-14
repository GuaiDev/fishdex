"""Trip-time conditions: the right owner, the cache, and the gauge snapshot.

The Open-Meteo archive reply is a recorded fixture (a real response for
2026-06-03..10 near Oakville). The WSC gauge is a canned reading. Nothing here
touches the network, and every database is temporary.
"""

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.stream_gauge import NearestGauge, StreamGaugeReading
from src.services import trip_enrichment_conditions as tec
from src.storage.database import get_db

FIXTURES = Path(__file__).parent / "fixtures"
weather = importlib.import_module("src.ingest.global.weather")


@pytest.fixture
def db(tmp_path):
    return get_db(path=tmp_path / "test.db")


@pytest.fixture
def archive_http(tmp_path, monkeypatch):
    """The recorded archive reply behind httpx, with the cache in a temp dir."""
    monkeypatch.setattr(weather, "_CACHE_DIR", tmp_path / "open-meteo-cache")
    reply = MagicMock()
    reply.json.return_value = json.loads((FIXTURES / "open_meteo_archive_hourly.json").read_text())
    reply.raise_for_status.return_value = None
    with patch("httpx.get", return_value=reply) as get:
        yield get


def _gauge(observed: datetime, **over) -> NearestGauge:
    fields = dict(
        station_id="02HB001",
        station_name="CREDIT RIVER AT STREETSVILLE",
        river_name="Credit River",
        lat=43.58,
        lng=-79.71,
        jurisdiction="CA-ON",
        water_level_m=0.523,
        discharge_cms=18.3,
        level_trend="stable",
        discharge_trend="rising",
        level_grade=None,
        reading_datetime=observed,
    )
    fields.update(over)
    return NearestGauge(reading=StreamGaugeReading(**fields), distance_km=8.2)


def _session(db, user_id, date="2026-06-10") -> int:
    db["sessions"].insert({"date": date, "overall_notes": None, "user_id": user_id})
    return db.execute("SELECT MAX(id) FROM sessions").fetchone()[0]


def _row(db, session_id) -> dict:
    return next(db["session_conditions"].rows_where("session_id = ?", [session_id]))


# -- the Open-Meteo archive call goes through the cache ---------------------------


def test_archive_call_is_served_from_the_cache_the_second_time(archive_http):
    first = tec._fetch_open_meteo(43.5, -79.7, datetime(2026, 6, 10, 12, 0), timeout=3.0)
    second = tec._fetch_open_meteo(43.5, -79.7, datetime(2026, 6, 10, 12, 0), timeout=3.0)
    assert first == second
    assert archive_http.call_count == 1


def test_archive_values_still_parse_from_the_recorded_reply(archive_http):
    got = tec._fetch_open_meteo(43.5, -79.7, datetime(2026, 6, 10, 12, 0), timeout=3.0)
    assert got["air_temp_c"] == 26.9
    assert got["pressure_hpa"] == 988.6
    assert got["wind_speed_kmh"] == 13.4
    assert got["weather_code"] == 3
    assert len(got["precip_history"]) == 192


def test_archive_request_keeps_the_timezone_and_wind_unit(archive_http):
    tec._fetch_open_meteo(43.5, -79.7, datetime(2026, 6, 10, 12, 0), timeout=3.0)
    params = archive_http.call_args.kwargs["params"]
    assert params["timezone"] == "America/Toronto"
    assert params["wind_speed_unit"] == "kmh"
    assert params["start_date"] == "2026-06-03" and params["end_date"] == "2026-06-10"
    assert archive_http.call_args.kwargs["timeout"] == 3.0  # the caller's budget, not 30 s


def test_the_trip_time_baseline_lookup_is_cached_too(archive_http):
    dt = datetime(2026, 6, 10, 12, 0)
    tec._fetch_historical_air_temp_baseline(43.5, -79.7, dt)
    calls = archive_http.call_count
    assert calls == 3  # one per prior year
    tec._fetch_historical_air_temp_baseline(43.5, -79.7, dt)
    assert archive_http.call_count == calls


def test_a_failed_archive_fetch_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "_CACHE_DIR", tmp_path / "c")
    with patch("httpx.get", side_effect=RuntimeError("offline")):
        with pytest.raises(RuntimeError, match="Open-Meteo fetch failed"):
            tec._fetch_open_meteo(43.5, -79.7, datetime(2026, 6, 10, 12), timeout=1.0)


# -- the row is written for the real user -------------------------------------------


@pytest.fixture
def quiet_sources(archive_http):
    """Archive weather from the fixture, no gauge. Tests override the gauge."""
    with patch("src.services.stream_gauge.nearest_gauge", return_value=None) as g:
        yield g


def test_enrichment_writes_the_sessions_real_user_id(db, quiet_sources):
    sid = _session(db, user_id=3)
    result = tec.enrich_session_conditions(
        db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12), user_id=3
    )
    assert result["stored"] is True
    assert _row(db, sid)["user_id"] == 3


def test_two_users_trips_keep_their_own_user_ids(db, quiet_sources):
    mine, theirs = _session(db, 2), _session(db, 3)
    for sid, uid in ((mine, 2), (theirs, 3)):
        tec.enrich_session_conditions(db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12), user_id=uid)
    assert _row(db, mine)["user_id"] == 2
    assert _row(db, theirs)["user_id"] == 3


def test_user_id_is_required_not_defaulted(db):
    """The column's own default is user 1; a caller that forgot it stamped everyone as 1."""
    sid = _session(db, 3)
    with pytest.raises(TypeError):
        tec.enrich_session_conditions(db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12))


def test_log_session_enriches_under_the_logging_users_id(db, archive_http, monkeypatch):
    """End to end through the real logger, not just the enrichment function."""
    from src.services.trip_logger import log_session

    monkeypatch.setattr("src.services.trip_enrichment.enrich_session", lambda *a, **k: {})
    db["users"].insert({"id": 3, "username": "bob", "role": "beta"})
    with patch("src.services.stream_gauge.nearest_gauge", return_value=None):
        out = log_session(
            {
                "date": "2026-06-10",
                "stops": [{"location_text": "creek", "lat": 43.5, "lng": -79.7}],
            },
            db,
            user_id=3,
        )
    assert _row(db, out["session_id"])["user_id"] == 3


# -- the migration repairs rows the old code stamped as user 1 -------------------------


def test_migration_repairs_rows_stamped_with_the_default_user(db):
    from src.storage.database import migrate_session_conditions_gauge

    sid = _session(db, user_id=3)
    db["session_conditions"].insert({"session_id": sid, "user_id": 1})
    orphan = db["session_conditions"].insert({"session_id": 9999, "user_id": 1}).last_pk
    migrate_session_conditions_gauge(db)
    assert _row(db, sid)["user_id"] == 3
    assert next(db["session_conditions"].rows_where("id = ?", [orphan]))["user_id"] == 1


def test_migration_is_idempotent_and_adds_the_gauge_columns(db):
    from src.storage.database import migrate_session_conditions_gauge

    migrate_session_conditions_gauge(db)
    migrate_session_conditions_gauge(db)
    cols = {c.name for c in db["session_conditions"].columns}
    assert {
        "flow_cms",
        "flow_trend",
        "level_m",
        "gauge_station_id",
        "gauge_station_name",
        "gauge_dist_km",
        "gauge_observed_at",
    } <= cols


# -- the gauge snapshot ---------------------------------------------------------------


def test_a_new_trip_snapshots_flow_and_level_from_the_nearest_gauge(db, archive_http):
    observed = datetime.now(UTC) - timedelta(minutes=10)
    sid = _session(db, 1, date=datetime.now().date().isoformat())
    with patch("src.services.stream_gauge.nearest_gauge", return_value=_gauge(observed)):
        result = tec.enrich_session_conditions(
            db, sid, 43.5, -79.7, datetime.now().replace(microsecond=0), user_id=1
        )
    row = _row(db, sid)
    assert row["flow_cms"] == 18.3
    assert row["level_m"] == 0.523
    assert row["flow_trend"] == "rising"
    assert row["gauge_station_id"] == "02HB001"
    assert row["gauge_station_name"] == "CREDIT RIVER AT STREETSVILLE"
    assert row["gauge_dist_km"] == 8.2
    assert datetime.fromisoformat(row["gauge_observed_at"]) == observed
    assert "wsc" in row["enrichment_source"]
    assert result["gauge"]["station_id"] == "02HB001"


def test_a_stable_gauge_trend_is_stored_as_steady(db, archive_http):
    observed = datetime.now(UTC)
    sid = _session(db, 1)
    gauge = _gauge(observed, discharge_trend="stable")
    with patch("src.services.stream_gauge.nearest_gauge", return_value=gauge):
        tec.enrich_session_conditions(db, sid, 43.5, -79.7, datetime.now(), user_id=1)
    assert _row(db, sid)["flow_trend"] == "steady"


def test_a_trip_logged_days_later_is_not_stamped_with_todays_river(db, archive_http):
    """The feed only says 'now'. Recording it as last week's flow would be false."""
    sid = _session(db, 1, date="2026-06-03")
    gauge = _gauge(datetime.now(UTC))
    with patch("src.services.stream_gauge.nearest_gauge", return_value=gauge):
        tec.enrich_session_conditions(
            db, sid, 43.5, -79.7, datetime.now() - timedelta(days=5), user_id=1
        )
    row = _row(db, sid)
    assert row["flow_cms"] is None and row["gauge_station_id"] is None
    assert "wsc" not in row["enrichment_source"]


def test_no_gauge_in_range_leaves_the_snapshot_null(db, quiet_sources):
    sid = _session(db, 1)
    tec.enrich_session_conditions(db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12), user_id=1)
    row = _row(db, sid)
    assert row["flow_cms"] is None and row["gauge_observed_at"] is None


def test_a_gauge_outage_does_not_stop_the_weather_from_being_stored(db, archive_http):
    sid = _session(db, 1)
    with patch("src.services.stream_gauge.nearest_gauge", side_effect=RuntimeError("wsc down")):
        result = tec.enrich_session_conditions(
            db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12), user_id=1
        )
    assert "wsc down" in result["gauge_error"]
    row = _row(db, sid)
    assert row["air_temp_c"] == 26.9 and row["flow_cms"] is None


def test_the_snapshot_uses_the_enrichment_timeout(db, archive_http):
    sid = _session(db, 1)
    with patch("src.services.stream_gauge.nearest_gauge", return_value=None) as gauge:
        tec.enrich_session_conditions(
            db, sid, 43.5, -79.7, datetime(2026, 6, 10, 12), timeout_seconds=2.5, user_id=1
        )
    assert gauge.call_args.kwargs["timeout"] == 2.5
