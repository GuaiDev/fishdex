"""A pin's flow row comes from a gauge on the pin's own water, or says whose water it is.

No network: the gauge network is faked at the adapter boundary, and the hydro
network is a handful of rows in a temporary database.
"""

import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from src.models.stream_gauge import StreamGaugeReading
from src.services import stream_gauge
from src.services.context import describe
from src.services.context import place as place_mod
from src.services.pins.conditions import _label
from src.services.pins.labels import CONDITION_FIELDS, labels_payload
from src.services.water_names import normalize_water_name, same_water
from src.storage.database import get_db

wsc = importlib.import_module("src.ingest.global.wsc")

# The Credit River mouth; Cooksville Creek's gauge is nearer than the Credit's.
MOUTH = (43.5500, -79.5850)


# -- name identity ---------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Credit River", "CREDIT RIVER"),
        ("Credit River", "The Credit River"),
        ("Rivière Rouge", "RIVIERE ROUGE"),
        ("Rivière Rouge", "Rouge River"),
        ("Cooksville Creek", "Cooksville Cr."),
        ("Humber River", "Humber River (West Branch)"),
    ],
)
def test_the_same_water_under_different_spellings(a, b):
    assert same_water(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Credit River", "Cooksville Creek"),
        ("Credit River", "Credit Creek"),  # a creek is not the river of the same name
        ("Humber River", "East Humber River"),
        ("Credit River", None),
        (None, None),
        ("", ""),
    ],
)
def test_different_or_missing_names_are_not_the_same_water(a, b):
    assert not same_water(a, b)


def test_normalize_gives_nothing_to_compare_for_a_blank_name():
    assert normalize_water_name("  ") is None
    assert normalize_water_name(None) is None


# -- choosing the gauge ------------------------------------------------------------

STATIONS = [
    # (id, name, river, lat, lng): Cooksville is the nearest; the Credit's are further up.
    ("02HB025", "COOKSVILLE CREEK AT COOKSVILLE", "Cooksville Creek", 43.58, -79.62),
    ("02HB001", "CREDIT RIVER NEAR STREETSVILLE", "Credit River", 43.58, -79.71),
    ("02HB002", "CREDIT RIVER ABOVE FAR", "Credit River", 43.90, -79.90),
    ("02HC033", "MIMICO CREEK AT ISLINGTON", "Mimico Creek", 43.63, -79.52),
]


@pytest.fixture
def network(monkeypatch):
    offline: set[str] = set()
    tried: list[str] = []

    def fetch_stations(lat, lng, radius_km=50, timeout=30):
        return [
            {
                "STATION_NUMBER": sid,
                "STATION_NAME": name,
                "RIVER_NAME": river,
                "LATITUDE": slat,
                "LONGITUDE": slng,
            }
            for sid, name, river, slat, slng in STATIONS
        ]

    def fetch_reading(station_id, lat, lng, timeout=30):
        tried.append(station_id)
        if station_id in offline:
            return None
        name = next(s for s in STATIONS if s[0] == station_id)
        return StreamGaugeReading(
            station_id=station_id,
            station_name=name[1],
            river_name=name[2],
            lat=lat,
            lng=lng,
            jurisdiction="CA-ON",
            water_level_m=0.5,
            discharge_cms=10.0,
            level_trend="stable",
            discharge_trend="rising",
            level_grade=None,
            reading_datetime=datetime.now(UTC) - timedelta(minutes=5),
        )

    monkeypatch.setattr(wsc, "fetch_nearby_stations", fetch_stations)
    monkeypatch.setattr(wsc, "fetch_station_reading", fetch_reading)
    return {"offline": offline, "tried": tried}


def test_without_a_known_water_the_nearest_gauge_wins_and_says_nothing_about_water(network):
    found = stream_gauge.nearest_gauge(*MOUTH)
    assert found.reading.station_id == "02HB025"
    assert found.same_water is None


def test_a_gauge_on_the_pins_own_river_beats_a_nearer_gauge_on_another_creek(network):
    found = stream_gauge.nearest_gauge(*MOUTH, own_water=["Credit River"])
    assert found.reading.station_id == "02HB001"
    assert found.same_water is True
    assert network["tried"] == ["02HB001"]  # the Cooksville gauge was never even read


def test_an_own_river_gauge_past_the_distance_cap_is_not_used(network):
    found = stream_gauge.nearest_gauge(*MOUTH, own_water=["Credit River"], own_water_max_km=5)
    assert found.reading.station_id == "02HB025"
    assert found.same_water is False


def test_an_offline_own_river_gauge_falls_back_and_says_so(network):
    network["offline"].add("02HB001")
    found = stream_gauge.nearest_gauge(*MOUTH, own_water=["Credit River"])
    assert found.reading.station_id == "02HB025"
    assert found.same_water is False


def test_no_gauge_on_the_pins_river_is_marked_as_another_water(network):
    found = stream_gauge.nearest_gauge(*MOUTH, own_water=["Etobicoke Creek"])
    assert found.reading.station_id == "02HB025"
    assert found.same_water is False


def test_any_of_the_waters_at_a_confluence_counts(network):
    found = stream_gauge.nearest_gauge(*MOUTH, own_water=["Etobicoke Creek", "Credit River"])
    assert found.same_water is True and found.reading.station_id == "02HB001"


# -- the pin's own water from the hydro network ------------------------------------


def _segment(db, ogf_id, name, lat, lng, dlat=0.0005):
    db["stream_segments"].insert(
        {
            "ogf_id": ogf_id,
            "name": name,
            "watercourse_type": "Stream",
            "geom_wkt": f"LINESTRING({lng} {lat - dlat}, {lng} {lat + dlat})",
            "stream_order": 4,
            "jurisdiction": "CA-ON",
        }
    )


@pytest.fixture
def db(tmp_path):
    db = get_db(tmp_path / "gauge.db")
    _segment(db, 1, "Credit River", MOUTH[0], MOUTH[1])
    _segment(db, 2, "Far Creek", MOUTH[0] + 0.02, MOUTH[1])  # about 2.2 km away
    _segment(db, 3, None, MOUTH[0], MOUTH[1])  # unnamed water has no identity to match
    return db


def test_the_pins_waters_are_the_named_ones_that_pass_close_by(db):
    place = place_mod.resolve(db, lat=MOUTH[0], lng=MOUTH[1])
    among = ["CREDIT RIVER", "Far Creek", "Nowhere Creek"]
    assert place_mod.water_names_at(db, place, among) == ["Credit River"]
    assert place_mod.water_names_at(db, place, among, within_km=5) == ["Credit River", "Far Creek"]


def test_the_pins_river_is_found_however_many_other_segments_surround_it(db):
    for i in range(10, 710):
        _segment(db, i, f"Other {i} Creek", MOUTH[0] + 0.01, MOUTH[1])
    place = place_mod.resolve(db, lat=MOUTH[0], lng=MOUTH[1])
    assert place_mod.water_names_at(db, place, ["Credit River"]) == ["Credit River"]


def test_no_hydro_table_means_no_names_not_an_error(tmp_path):
    empty = get_db(tmp_path / "empty.db")
    place = place_mod.resolve(empty, lat=MOUTH[0], lng=MOUTH[1])
    assert place_mod.water_names_at(empty, place, ['Credit River']) is None


# -- the panel ---------------------------------------------------------------------


def _weather(**over):
    raw = {
        "time": (datetime.now(UTC) - timedelta(minutes=10))
        .replace(tzinfo=None)
        .isoformat(timespec="minutes"),
        "temperature_c": 10.0,
        "wind_speed_kmh": 14.0,
        "precipitation_mm": 0.2,
        "pressure_hpa": 1011.0,
        "pressure_trend": "rising",
    }
    raw.update(over)
    return json.dumps(raw)


@pytest.fixture
def panel(db, network, monkeypatch):
    monkeypatch.setattr("src.services.weather.get_conditions_for_agent", lambda **kw: _weather())

    def rows(lat, lng):
        from src.models.pin import PinConditionRow
        from src.services.pins.conditions import _row

        ctx = describe(db, lat=lat, lng=lng, caller="pin_panel")
        now = datetime.now(UTC)
        return {
            spec["field"]: _row(spec, getattr(ctx.conditions, spec["field"]), now)
            for spec in CONDITION_FIELDS
        }, PinConditionRow

    return rows


def test_a_pin_on_the_credit_gets_the_credit_gauge_and_a_label_that_names_it(panel):
    rows, _ = panel(*MOUTH)
    flow = rows["flow_cms"]
    assert flow.station == "CREDIT RIVER NEAR STREETSVILLE"
    assert flow.station_water == "Credit River"
    assert flow.on_pin_water is True
    assert flow.label == "Flow on Credit River"
    assert rows["flow_trend"].label == "Flow trend on Credit River"
    assert rows["level_m"].label == "Water level on Credit River"


def test_a_pin_with_no_gauge_on_its_river_says_whose_water_the_row_is(db, panel):
    _segment(db, 9, "Etobicoke Creek", 43.70, -79.60)
    rows, _ = panel(43.70, -79.60)
    flow = rows["flow_cms"]
    assert flow.station_water == "Cooksville Creek" or flow.station_water == "Mimico Creek"
    assert flow.on_pin_water is False
    assert flow.label.startswith(f"Flow on {flow.station_water}, ")
    assert flow.label.endswith(f"{flow.distance_km:g} km away")
    assert "River flow" not in flow.label


def test_a_pin_whose_water_is_unknown_gets_the_nearest_gauge_named_for_its_own_water(
    tmp_path, network, monkeypatch
):
    bare = get_db(tmp_path / "bare.db")
    monkeypatch.setattr("src.services.weather.get_conditions_for_agent", lambda **kw: _weather())
    ctx = describe(bare, lat=MOUTH[0], lng=MOUTH[1], caller="pin_panel")
    prov = ctx.conditions.flow_cms.provenance
    assert prov.water == "Cooksville Creek"
    assert prov.same_water is None
    spec = next(s for s in CONDITION_FIELDS if s["field"] == "flow_cms")
    assert _label(spec, ctx.conditions.flow_cms) == "Flow on Cooksville Creek, 4.4 km away"


def test_an_empty_gauge_row_keeps_the_plain_label(panel, network):
    rows, _ = panel(MOUTH[0], MOUTH[1])
    network["offline"].update({s[0] for s in STATIONS})
    rows, _ = panel(MOUTH[0], MOUTH[1])
    assert rows["flow_cms"].label == "Flow"
    assert rows["flow_cms"].empty_reason == "no_records_in_radius"


def test_the_pressure_row_keeps_the_reading_and_makes_no_claim_about_the_bite(panel):
    rows, _ = panel(*MOUTH)
    assert rows["pressure_trend"].value == "rising"
    assert rows["pressure_trend"].meaning is None
    assert rows["pressure_hpa"].value == 1011.0


def test_the_labels_endpoint_serves_the_gauge_wording(panel):
    payload = labels_payload()
    assert payload["gauge_water_labels"]["flow_cms"] == "Flow on {water}"
    assert "{km" in payload["gauge_distance_suffix"]
    flow = next(f for f in payload["condition_fields"] if f["field"] == "flow_cms")
    assert flow["label"] == "Flow"
