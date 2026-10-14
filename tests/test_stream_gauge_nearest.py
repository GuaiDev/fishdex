"""nearest_gauge: the closest station that reports, with its distance.

The station list is the recorded WSC fixture; readings are the recorded reading
fixture re-labelled per station. No network.
"""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.models.stream_gauge import StreamGaugeReading
from src.services import stream_gauge

wsc = importlib.import_module("src.ingest.global.wsc")
FIXTURES = Path(__file__).parent / "fixtures"

# Streetsville (02HB001) is about 2 km from here; Weston (02HC024) about 20 km.
NEAR_STREETSVILLE = (43.57, -79.72)


@pytest.fixture
def stations(monkeypatch):
    data = json.loads((FIXTURES / "wsc_stations.json").read_text())
    calls = {"stations": [], "readings": []}

    def fetch_stations(lat, lng, radius_km=50, timeout=30):
        calls["stations"].append(timeout)
        return _normalise(data)

    def _normalise(payload):
        out = []
        for f in payload["features"]:
            p = f["properties"]
            out.append(
                {
                    "STATION_NUMBER": p["STATION_NUMBER"],
                    "STATION_NAME": p["STATION_NAME"],
                    "LATITUDE": f["geometry"]["coordinates"][1],
                    "LONGITUDE": f["geometry"]["coordinates"][0],
                }
            )
        return out

    offline: set[str] = set()

    def fetch_reading(station_id, lat, lng, timeout=30):
        calls["readings"].append((station_id, timeout))
        if station_id in offline:
            return None
        return StreamGaugeReading(
            station_id=station_id,
            station_name=f"STATION {station_id}",
            river_name=None,
            lat=lat,
            lng=lng,
            jurisdiction="CA-ON",
            water_level_m=0.5,
            discharge_cms=10.0,
            level_trend="stable",
            discharge_trend="rising",
            level_grade=None,
            reading_datetime=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        )

    monkeypatch.setattr(wsc, "fetch_nearby_stations", fetch_stations)
    monkeypatch.setattr(wsc, "fetch_station_reading", fetch_reading)
    return {"calls": calls, "offline": offline}


def test_returns_the_nearest_reporting_station_with_its_distance(stations):
    found = stream_gauge.nearest_gauge(*NEAR_STREETSVILLE)
    assert found.reading.station_id == "02HB001"
    assert 0 < found.distance_km < 5


def test_an_offline_nearest_station_falls_through_to_the_next(stations):
    stations["offline"].add("02HB001")
    found = stream_gauge.nearest_gauge(*NEAR_STREETSVILLE)
    assert found.reading.station_id == "02HC024"
    assert found.distance_km > 10  # the distance is the one for the station used


def test_no_station_reporting_in_the_first_few_is_none(stations):
    stations["offline"].update({"02HB001", "02HC024", "02HC003"})
    assert stream_gauge.nearest_gauge(*NEAR_STREETSVILLE) is None


def test_only_the_closest_few_are_tried(stations):
    stations["offline"].update({"02HB001", "02HC024", "02HC003"})
    stream_gauge.nearest_gauge(*NEAR_STREETSVILLE, max_stations=2)
    assert len(stations["calls"]["readings"]) == 2


def test_no_station_in_range_is_none(monkeypatch):
    monkeypatch.setattr(wsc, "fetch_nearby_stations", lambda *a, **k: [])
    assert stream_gauge.nearest_gauge(60.0, -100.0) is None


def test_an_unreachable_service_raises_so_callers_can_say_try_again(monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("wsc unreachable")

    monkeypatch.setattr(wsc, "fetch_nearby_stations", down)
    with pytest.raises(httpx.ConnectError):
        stream_gauge.nearest_gauge(*NEAR_STREETSVILLE)


def test_the_callers_timeout_reaches_both_wsc_calls(stations):
    stream_gauge.nearest_gauge(*NEAR_STREETSVILLE, timeout=2.5)
    assert stations["calls"]["stations"] == [2.5]
    assert stations["calls"]["readings"][0][1] == 2.5


def test_stations_without_coordinates_are_skipped(monkeypatch, stations):
    monkeypatch.setattr(
        wsc,
        "fetch_nearby_stations",
        lambda *a, **k: [
            {"STATION_NUMBER": "NOLOC", "LATITUDE": None, "LONGITUDE": None},
            {"STATION_NUMBER": "02HB001", "LATITUDE": 43.5833, "LONGITUDE": -79.7167},
        ],
    )
    assert stream_gauge.nearest_gauge(*NEAR_STREETSVILLE).reading.station_id == "02HB001"
