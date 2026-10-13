"""Tests for StreamGaugeReading and StreamGaugeSummary models."""

from datetime import datetime

import pytest

from src.models.stream_gauge import StreamGaugeReading, StreamGaugeSummary


def _reading(**kwargs) -> StreamGaugeReading:
    defaults = {
        "station_id": "02HB001",
        "station_name": "CREDIT RIVER AT STREETSVILLE",
        "river_name": "Credit River",
        "lat": 43.5833,
        "lng": -79.7167,
        "jurisdiction": "CA-ON",
        "water_level_m": 0.523,
        "discharge_cms": 18.3,
        "level_trend": "rising",
        "discharge_trend": "rising",
        "level_grade": " ",
        "reading_datetime": datetime(2026, 5, 22, 14, 0, 0),
    }
    defaults.update(kwargs)
    return StreamGaugeReading(**defaults)


def test_stream_gauge_reading_valid():
    r = _reading()
    assert r.station_id == "02HB001"
    assert r.water_level_m == pytest.approx(0.523)
    assert r.discharge_cms == pytest.approx(18.3)
    assert r.level_trend == "rising"
    assert r.discharge_trend == "rising"
    assert r.jurisdiction == "CA-ON"


def test_stream_gauge_reading_optional_fields():
    r = _reading(
        river_name=None,
        water_level_m=None,
        discharge_cms=None,
        level_trend=None,
        discharge_trend=None,
        level_grade=None,
    )
    assert r.river_name is None
    assert r.water_level_m is None
    assert r.discharge_cms is None
    assert r.level_trend is None
    assert r.discharge_trend is None


def test_stream_gauge_reading_fetched_at_defaults_to_now():
    r = _reading()
    assert r.fetched_at is not None
    assert isinstance(r.fetched_at, datetime)


def test_stream_gauge_reading_24hr_means_optional():
    r = _reading(level_24hr_mean_m=0.45, discharge_24hr_mean_cms=15.0)
    assert r.level_24hr_mean_m == pytest.approx(0.45)
    assert r.discharge_24hr_mean_cms == pytest.approx(15.0)

    r2 = _reading()
    assert r2.level_24hr_mean_m is None
    assert r2.discharge_24hr_mean_cms is None


def test_stream_gauge_summary_valid():
    s = StreamGaugeSummary(
        station_id="02HB001",
        station_name="CREDIT RIVER AT STREETSVILLE",
        river_name="Credit River",
        current_level_m=0.523,
        current_discharge_cms=18.3,
        level_trend="rising",
        discharge_trend="rising",
        condition_note="elevated and rising",
        fishing_note=(
            "Rising, elevated water — fish tight to structure, avoid main current, "
            "try slower backwater areas. Clarity likely reduced."
        ),
        distance_km=12.4,
        reading_datetime=datetime(2026, 5, 22, 14, 0, 0),
        fetched_at=datetime(2026, 5, 22, 14, 5, 0),
    )
    assert s.station_id == "02HB001"
    assert s.condition_note == "elevated and rising"
    assert s.distance_km == pytest.approx(12.4)


def test_stream_gauge_summary_null_level_and_discharge():
    s = StreamGaugeSummary(
        station_id="02HB001",
        station_name="CREDIT RIVER AT STREETSVILLE",
        river_name=None,
        current_level_m=None,
        current_discharge_cms=None,
        level_trend=None,
        discharge_trend=None,
        condition_note="normal and stable",
        fishing_note="Normal stable flow — standard conditions for this system.",
        distance_km=5.0,
        reading_datetime=datetime(2026, 5, 22, 14, 0, 0),
        fetched_at=datetime(2026, 5, 22, 14, 5, 0),
    )
    assert s.current_level_m is None
    assert s.current_discharge_cms is None


def test_invalid_trend_value_raises():
    with pytest.raises(Exception):
        _reading(level_trend="unknown")


def test_nearest_gauge_pairs_a_reading_with_its_distance():
    from src.models.stream_gauge import NearestGauge

    near = NearestGauge(reading=_reading(), distance_km=8.2)
    assert near.distance_km == 8.2
    assert near.reading.station_id == "02HB001"
    with pytest.raises(Exception):
        NearestGauge(reading=_reading())  # the distance is not optional
