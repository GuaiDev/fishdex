"""Source and age on every conditions value, and stale values that stay visible.

No network: weather and the WSC gauge are replaced with canned readings whose
times are set relative to now, so "fresh" and "stale" are real comparisons
against the named limits and not against a frozen date.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.models.context import ContextField, EmptyReason, Provenance, ProvenanceKind
from src.models.freshness import (
    GAUGE_STALE_S,
    WATER_QUALITY_STALE_S,
    WEATHER_STALE_S,
    age_seconds,
    is_stale,
    normalise_observed_at,
    parse_observed_at,
)
from src.models.stream_gauge import NearestGauge, StreamGaugeReading
from src.services.context import describe, render
from src.services.context.slices import build_conditions

NOW = datetime.now(UTC)
HOME = (43.40, -79.75)


def _ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


# -- the limits ----------------------------------------------------------------


def test_stale_limits_are_the_agreed_named_constants():
    assert WEATHER_STALE_S == 2 * 3600
    assert GAUGE_STALE_S == 6 * 3600
    assert WATER_QUALITY_STALE_S == 30 * 86400


def test_time_parsing_reads_naive_as_utc_and_dates_as_dates():
    assert parse_observed_at("2026-05-20T12:00") == datetime(2026, 5, 20, 12, tzinfo=UTC)
    assert parse_observed_at("2026-05-22T14:00:00Z") == datetime(2026, 5, 22, 14, tzinfo=UTC)
    assert parse_observed_at("2026-05-22T10:00:00-04:00") == datetime(2026, 5, 22, 14, tzinfo=UTC)
    assert parse_observed_at("2024-06-01") == datetime(2024, 6, 1, tzinfo=UTC)
    assert parse_observed_at("not a time") is None
    assert parse_observed_at(None) is None


def test_a_bare_date_stays_a_bare_date():
    """Padding to midnight would claim an hour nobody measured."""
    assert normalise_observed_at("2024-06-01") == "2024-06-01"
    assert normalise_observed_at(datetime(2024, 6, 1).date()) == "2024-06-01"
    assert normalise_observed_at(datetime(2024, 6, 1, 9, 30)) == "2024-06-01T09:30:00+00:00"
    assert normalise_observed_at("garbage") is None


def test_age_is_never_negative_and_unreadable_is_none():
    assert age_seconds(_ago(minutes=5).isoformat(), NOW) == 300
    assert age_seconds((NOW + timedelta(hours=1)).isoformat(), NOW) == 0
    assert age_seconds("garbage", NOW) is None


def test_undated_reading_cannot_be_called_fresh():
    assert is_stale(None, WEATHER_STALE_S) is True
    assert is_stale(_ago(hours=1).isoformat(), WEATHER_STALE_S, NOW) is False
    assert is_stale(_ago(hours=3).isoformat(), WEATHER_STALE_S, NOW) is True


# -- ContextField --------------------------------------------------------------


def test_recorded_field_carries_time_station_distance_and_staleness():
    field = ContextField.recorded(
        12.4,
        source="WSC station 02HB001",
        station="CREDIT RIVER AT STREETSVILLE",
        distance_km=8.2,
        observed_at=_ago(hours=7),
        stale_after_s=GAUGE_STALE_S,
        now=NOW,
    )
    assert field.stale is True
    assert field.value == 12.4  # a stale value is shown, not emptied
    assert field.empty_reason is None
    assert field.provenance.station == "CREDIT RIVER AT STREETSVILLE"
    assert field.provenance.distance_km == 8.2
    text = field.explain()
    assert "stale" in text and "8.2 km away" in text and "CREDIT RIVER" in text


def test_fresh_field_is_not_marked_stale():
    field = ContextField.recorded(
        3.1,
        source="Open-Meteo",
        observed_at=_ago(minutes=20),
        stale_after_s=WEATHER_STALE_S,
        now=NOW,
    )
    assert field.stale is False
    assert "stale" not in field.explain()


def test_field_without_a_limit_is_never_stale():
    """Substrate, stream order and the rest have no age to speak of."""
    field = ContextField.recorded("coarse", source="Ontario surficial geology")
    assert field.stale is False
    assert field.provenance.observed_at is None


def test_empty_field_is_not_stale_and_keeps_its_reason():
    field = ContextField.empty(EmptyReason.NO_LIVE_SOURCE)
    assert field.stale is False
    assert "no live source" in field.explain()


def test_no_live_source_is_not_a_coverage_gap_or_a_retry():
    text = ContextField.empty(EmptyReason.NO_LIVE_SOURCE).explain()
    assert "does not cover" not in text
    assert "try again" not in text


def test_web_provenance_is_still_never_verified():
    prov = Provenance(
        kind=ProvenanceKind.WEB, source="http://x", verified=True, observed_at="2026-01-01"
    )
    assert prov.verified is False


# -- the conditions slice ------------------------------------------------------


def _weather(observed: datetime, **over) -> str:
    raw = {
        "when": "now",
        "time": observed.replace(tzinfo=None).isoformat(timespec="minutes"),
        "temperature_c": 10.0,
        "wind_speed_kmh": 14.0,
        "precipitation_mm": 0.2,
        "pressure_hpa": 1011.0,
        "pressure_trend": "falling",
    }
    raw.update(over)
    return json.dumps(raw)


def _gauge(observed: datetime, dist=6.4, **over) -> NearestGauge:
    fields = dict(
        station_id="02HB001",
        station_name="CREDIT RIVER AT STREETSVILLE",
        river_name="Credit River",
        lat=43.58,
        lng=-79.71,
        jurisdiction="CA-ON",
        water_level_m=0.52,
        discharge_cms=18.3,
        level_trend="stable",
        discharge_trend="rising",
        level_grade=None,
        reading_datetime=observed,
    )
    fields.update(over)
    return NearestGauge(reading=StreamGaugeReading(**fields), distance_km=dist)


@pytest.fixture
def sources(monkeypatch):
    """Set what the live sources return; defaults are fresh weather and a fresh gauge."""
    state = {"weather": _weather(_ago(minutes=10)), "gauge": _gauge(_ago(minutes=5))}

    def weather(**kwargs):
        if isinstance(state["weather"], Exception):
            raise state["weather"]
        return state["weather"]

    def gauge(*args, **kwargs):
        if isinstance(state["gauge"], Exception):
            raise state["gauge"]
        return state["gauge"]

    monkeypatch.setattr("src.services.weather.get_conditions_for_agent", weather)
    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", gauge)
    return state


def _slice(db, now=None):
    ctx = describe(db, lat=HOME[0], lng=HOME[1], caller="pin_panel")
    return ctx.conditions


def test_pin_panel_bundle_is_conditions_only(db, sources):
    ctx = describe(db, lat=HOME[0], lng=HOME[1], caller="pin_panel")
    assert ctx.bundle == "pin_panel"
    assert ctx.conditions is not None
    assert ctx.records is None and ctx.water is None and ctx.history is None


def test_fresh_weather_has_source_time_and_no_stale_flag(db, sources):
    c = _slice(db)
    assert c.air_temp_c.value == 10.0
    assert c.air_temp_c.provenance.source == "Open-Meteo"
    assert c.air_temp_c.provenance.observed_at is not None
    assert not c.air_temp_c.stale
    assert c.wind_speed_kmh.value == 14.0
    assert c.pressure_hpa.value == 1011.0
    assert c.pressure_trend.value == "falling"


def test_weather_older_than_two_hours_is_stale_but_shown(db, sources):
    sources["weather"] = _weather(_ago(hours=3))
    c = _slice(db)
    assert c.air_temp_c.value == 10.0
    assert c.air_temp_c.stale is True
    assert c.wind_speed_kmh.stale is True


def test_zero_degrees_is_a_temperature_not_a_missing_one(db, sources):
    """`temperature_c or air_temp_c` read 0.0 as absent, in October and April."""
    sources["weather"] = _weather(_ago(minutes=10), temperature_c=0.0)
    assert _slice(db).air_temp_c.value == 0.0


def test_weather_outage_reads_as_transient_not_missing_coverage(db, sources):
    sources["weather"] = RuntimeError("down")
    c = _slice(db)
    assert c.air_temp_c.empty_reason is EmptyReason.LIVE_LOOKUP_FAILED
    assert c.pressure_trend.empty_reason is EmptyReason.LIVE_LOOKUP_FAILED
    # and the gauge is unaffected by it
    assert c.flow_cms.value == 18.3


def test_flow_comes_from_the_nearest_gauge_with_name_distance_and_time(db, sources):
    c = _slice(db)
    assert c.flow_cms.value == 18.3
    assert c.level_m.value == 0.52
    prov = c.flow_cms.provenance
    assert prov.source == "WSC station 02HB001"
    assert prov.station == "CREDIT RIVER AT STREETSVILLE"
    assert prov.distance_km == 6.4
    assert prov.observed_at is not None
    assert not c.flow_cms.stale


def test_flow_is_a_trend_in_the_panels_words_and_never_normal(db, sources):
    c = _slice(db)
    assert c.flow_trend.value == "rising"
    assert c.level_trend.value == "steady"  # the gauge says "stable"
    for f in (c.flow_cms, c.flow_trend, c.level_m, c.level_trend):
        assert "normal" not in f.explain().lower()
        assert f.provenance.station == "CREDIT RIVER AT STREETSVILLE"


def test_gauge_reading_older_than_six_hours_is_stale(db, sources):
    sources["gauge"] = _gauge(_ago(hours=7))
    c = _slice(db)
    assert c.flow_cms.stale is True
    assert c.flow_cms.value == 18.3


def test_gauge_just_inside_the_limit_is_not_stale(db, sources):
    sources["gauge"] = _gauge(_ago(hours=5, minutes=50))
    assert not _slice(db).flow_cms.stale


def test_no_gauge_in_range_says_so(db, sources):
    sources["gauge"] = None
    c = _slice(db)
    for f in (c.flow_cms, c.flow_trend, c.level_m, c.level_trend):
        assert f.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS


def test_gauge_outage_reads_as_transient(db, sources):
    sources["gauge"] = RuntimeError("wsc down")
    c = _slice(db)
    assert c.flow_cms.empty_reason is EmptyReason.LIVE_LOOKUP_FAILED
    assert c.air_temp_c.value == 10.0  # weather unaffected


def test_a_gauge_with_level_but_no_discharge_leaves_flow_empty_for_the_right_reason(db, sources):
    sources["gauge"] = _gauge(_ago(minutes=5), discharge_cms=None, discharge_trend=None)
    c = _slice(db)
    assert c.flow_cms.empty_reason is EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE
    assert c.flow_trend.empty_reason is EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE
    assert c.level_m.value == 0.52


def test_nothing_in_the_slice_is_the_old_not_wired_mislabel(db, sources):
    """Flow and water temperature were SOURCE_DOES_NOT_COVER_AREA for being unwired."""
    for state in (None, RuntimeError("x")):
        sources["gauge"] = state
        c = _slice(db)
        for name, f in c.__dict__.items():
            assert f.empty_reason is not EmptyReason.SOURCE_DOES_NOT_COVER_AREA, name
            assert f.value is not None or f.empty_reason is not None, name


# -- water temperature and chemistry: the dated nearest sample -------------------


def _sample(db, rid, station, lat, lng, when, **vals):
    row = {
        "record_id": rid,
        "station_id": station,
        "station_name": f"{station} creek",
        "lat": lat,
        "lng": lng,
        "jurisdiction": "CA-ON",
        "sampled_at": when,
        "do_mgl": None,
        "ph": None,
        "temp_c": None,
        "conductivity_us_cm": None,
        "turbidity_fnu": None,
    }
    row.update(vals)
    db["water_quality_readings"].insert(row, alter=True)


def test_water_temperature_is_honestly_empty_without_a_dated_sample(db, sources):
    c = _slice(db)
    assert c.water_temp_c.is_empty
    assert c.water_temp_c.empty_reason is EmptyReason.NO_LIVE_SOURCE


def test_water_temperature_comes_from_the_dated_sample_and_is_stale_when_old(db, sources):
    _sample(db, "W1", "S1", 43.41, -79.75, "2024-07-03", temp_c=19.5)
    c = _slice(db)
    assert c.water_temp_c.value == 19.5
    assert c.water_temp_c.stale is True
    prov = c.water_temp_c.provenance
    assert prov.observed_at == "2024-07-03"
    assert prov.station == "S1 creek"
    assert 0 < prov.distance_km < 3
    assert "not a live reading" in c.water_temp_c.meaning


def test_a_sample_from_last_week_is_not_stale(db, sources):
    last_week = (NOW - timedelta(days=7)).date().isoformat()
    _sample(db, "W1", "S1", 43.41, -79.75, last_week, temp_c=14.0)
    assert not _slice(db).water_temp_c.stale


def test_chemistry_is_the_nearest_sample_not_a_median_across_stations(db, sources):
    _sample(db, "near", "NEAR", 43.405, -79.75, "2023-05-01", do_mgl=9.0, ph=6.0)
    _sample(db, "far1", "FAR1", 43.55, -79.75, "2024-06-01", do_mgl=3.0, ph=8.0)
    _sample(db, "far2", "FAR2", 43.56, -79.75, "2024-06-02", do_mgl=4.0, ph=8.1)
    c = _slice(db)
    assert c.dissolved_oxygen_mgl.value == 9.0
    assert c.ph.value == 6.0
    assert c.dissolved_oxygen_mgl.provenance.station == "NEAR creek"
    assert c.dissolved_oxygen_mgl.provenance.observed_at == "2023-05-01"


def test_at_the_nearest_station_the_newest_sample_wins(db, sources):
    _sample(db, "old", "S1", 43.41, -79.75, "2019-05-01", do_mgl=5.0)
    _sample(db, "new", "S1", 43.41, -79.75, "2024-05-01", do_mgl=8.0)
    assert _slice(db).dissolved_oxygen_mgl.value == 8.0


def test_each_field_uses_its_own_nearest_sample(db, sources):
    _sample(db, "a", "A", 43.405, -79.75, "2024-05-01", do_mgl=7.0)  # no pH here
    _sample(db, "b", "B", 43.45, -79.75, "2024-05-02", ph=7.4)
    c = _slice(db)
    assert c.dissolved_oxygen_mgl.provenance.station == "A creek"
    assert c.ph.provenance.station == "B creek"


def test_the_panel_keeps_a_mid_range_ph_that_the_water_slice_withholds(db, sources):
    _sample(db, "W1", "S1", 43.41, -79.75, "2024-06-01", ph=7.2)
    assert _slice(db).ph.value == 7.2
    water = describe(db, lat=HOME[0], lng=HOME[1], caller="map_tap").water
    assert water.ph.empty_reason is EmptyReason.RECORDED_BUT_NOT_DECISION_RELEVANT


def test_water_slice_chemistry_is_now_dated_not_an_undated_median(db, sources):
    _sample(db, "W1", "S1", 43.41, -79.75, "2024-06-01", do_mgl=3.0)
    do = describe(db, lat=HOME[0], lng=HOME[1], caller="map_tap").water.dissolved_oxygen
    assert do.value == 3.0
    assert do.provenance.observed_at == "2024-06-01"
    assert do.stale is True
    assert "2024-06-01" in do.explain()


def test_no_samples_in_range_is_a_radius_gap(db, sources):
    _sample(db, "W1", "S1", 45.0, -80.5, "2024-06-01", do_mgl=3.0, ph=5.0)
    c = _slice(db)
    assert c.dissolved_oxygen_mgl.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS
    assert c.ph.empty_reason is EmptyReason.NO_RECORDS_IN_RADIUS


# -- the one renderer still speaks for every caller ------------------------------


def test_prompt_rendering_marks_stale_values_and_names_the_station(db, sources):
    sources["gauge"] = _gauge(_ago(hours=9))
    text = render.render_conditions(
        build_conditions(db, describe(db, lat=HOME[0], lng=HOME[1], caller="pin_panel").place)
    )
    assert "flow on Credit River" in text and "stale" in text
    assert "CREDIT RIVER AT STREETSVILLE" in text
    assert "normal" not in text.lower()


def test_bundles_for_chat_callers_still_get_conditions(db, sources):
    for caller in ("post_log", "coach", "full"):
        ctx = describe(db, lat=HOME[0], lng=HOME[1], caller=caller, escalate=False)
        assert ctx.conditions is not None, caller
        assert ctx.conditions.flow_cms.value == 18.3
