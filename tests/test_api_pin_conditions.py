"""GET /pins/{id}/conditions: shape, wording from the labels module, and who may read it.

Weather and the WSC gauge are canned (times set relative to now); every database
is temporary. The cross-user cases are the same standard as the other pin routes:
another user's pin answers 404, byte-identical to a pin that does not exist.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import _create_token
from src.models.context import EmptyReason
from src.models.pin import PinConditionRow
from src.models.stream_gauge import NearestGauge, StreamGaugeReading
from src.services.pins import labels
from src.storage.database import get_db

client = TestClient(app)
SPOT = (43.4000, -79.7500)
MISSING = 999_999
FIXTURE = Path(__file__).parent / "fixtures" / "pin_conditions_response.json"

ROW_KEYS = {
    "field",
    "label",
    "group",
    "value",
    "unit",
    "meaning",
    "source",
    "station",
    "distance_km",
    "station_water",
    "on_pin_water",
    "observed_at",
    "age_s",
    "age_label",
    "stale",
    "empty_reason",
}


def _gauge(observed: datetime) -> NearestGauge:
    return NearestGauge(
        reading=StreamGaugeReading(
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
        ),
        distance_km=8.2,
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")
    fresh = datetime.now(UTC) - timedelta(minutes=10)
    monkeypatch.setattr(
        "src.services.weather.get_conditions_for_agent",
        lambda **kw: json.dumps(
            {
                "time": fresh.replace(tzinfo=None).isoformat(timespec="minutes"),
                "temperature_c": 10.0,
                "wind_speed_kmh": 14.0,
                "precipitation_mm": 0.2,
                "pressure_hpa": 1011.0,
                "pressure_trend": "falling",
            }
        ),
    )
    monkeypatch.setattr(
        "src.services.stream_gauge.nearest_gauge",
        lambda *a, **k: _gauge(datetime.now(UTC) - timedelta(minutes=5)),
    )


@pytest.fixture()
def world():
    """Two users with one pin each."""
    db = get_db()
    db["users"].insert({"id": 2, "username": "alice", "role": "beta"})
    db["users"].insert({"id": 3, "username": "bob", "role": "beta"})
    out = {"db": db}
    for name, uid in (("a", 2), ("b", 3)):
        headers = {"Authorization": f"Bearer {_create_token(db, uid)}"}
        pin = client.post(
            "/pins",
            json={"name": f"{name}'s pin", "lat": SPOT[0], "lng": SPOT[1]},
            headers=headers,
        ).json()["pin"]
        out[name] = {"headers": headers, "pin_id": pin["id"]}
    return out


def _rows(world, me="a", pin=None):
    response = client.get(
        f"/pins/{pin or world[me]['pin_id']}/conditions", headers=world[me]["headers"]
    )
    assert response.status_code == 200, response.text
    return {r["field"]: r for r in response.json()}


# -- the response ------------------------------------------------------------------


def test_every_row_has_the_documented_keys(world):
    rows = client.get(f"/pins/{world['a']['pin_id']}/conditions", headers=world["a"]["headers"])
    for row in rows.json():
        assert set(row) == ROW_KEYS
        PinConditionRow.model_validate(row)


def test_rows_come_in_the_labels_modules_order(world):
    got = [
        r["field"]
        for r in client.get(
            f"/pins/{world['a']['pin_id']}/conditions", headers=world["a"]["headers"]
        ).json()
    ]
    assert got == [f["field"] for f in labels.CONDITION_FIELDS]


def test_weather_row_has_value_unit_source_and_age(world):
    row = _rows(world)["air_temp_c"]
    assert row["value"] == 10.0 and row["unit"] == "°C"
    assert row["source"] == "Open-Meteo"
    assert row["stale"] is False and row["empty_reason"] is None
    assert 500 < row["age_s"] < 700
    assert row["age_label"] == "10 min ago"
    assert row["observed_at"].startswith(str(datetime.now(UTC).year))


def test_flow_row_names_the_station_and_its_distance(world):
    row = _rows(world)["flow_cms"]
    assert row["value"] == 18.3 and row["unit"] == "m³/s"
    assert row["station"] == "CREDIT RIVER AT STREETSVILLE"
    assert row["distance_km"] == 8.2
    assert row["source"] == "WSC station 02HB001"


def test_flow_is_a_trend_and_the_word_normal_appears_nowhere(world):
    rows = _rows(world)
    assert rows["flow_trend"]["value"] == "rising"
    assert rows["level_trend"]["value"] == "steady"
    assert "normal" not in json.dumps(rows).lower()


def test_a_stale_value_is_shown_and_marked(world, monkeypatch):
    old = datetime.now(UTC) - timedelta(hours=7)
    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", lambda *a, **k: _gauge(old))
    row = _rows(world)["flow_cms"]
    assert row["stale"] is True
    assert row["value"] == 18.3
    assert row["empty_reason"] is None
    assert row["age_s"] > 6 * 3600


def test_water_temperature_without_a_sample_says_there_is_no_live_source(world):
    row = _rows(world)["water_temp_c"]
    assert row["value"] is None
    assert row["empty_reason"] == "no_live_source"
    assert row["stale"] is False and row["age_s"] is None and row["source"] is None


def test_a_dated_sample_fills_water_temperature_and_chemistry_as_stale(world):
    world["db"]["water_quality_readings"].insert(
        {
            "record_id": "W1",
            "station_id": "S1",
            "station_name": "Bronte Creek at Hwy 5",
            "lat": 43.41,
            "lng": -79.75,
            "jurisdiction": "CA-ON",
            "sampled_at": "2024-07-03",
            "do_mgl": 8.1,
            "ph": 7.4,
            "temp_c": 19.5,
            "conductivity_us_cm": None,
            "turbidity_fnu": None,
        },
        alter=True,
    )
    rows = _rows(world)
    assert rows["water_temp_c"]["value"] == 19.5
    assert rows["water_temp_c"]["station"] == "Bronte Creek at Hwy 5"
    assert rows["water_temp_c"]["observed_at"] == "2024-07-03"
    assert rows["water_temp_c"]["stale"] is True
    assert rows["dissolved_oxygen_mgl"]["value"] == 8.1
    assert rows["ph"]["value"] == 7.4
    assert rows["ph"]["unit"] is None


def test_a_source_outage_is_a_retry_not_a_gap(world, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", down)
    monkeypatch.setattr("src.services.weather.get_conditions_for_agent", down)
    rows = _rows(world)
    assert rows["flow_cms"]["empty_reason"] == "live_lookup_failed"
    assert rows["air_temp_c"]["empty_reason"] == "live_lookup_failed"


def test_every_empty_row_has_a_reason_the_ui_can_word(world, monkeypatch):
    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", lambda *a, **k: None)
    served = client.get("/pins/labels", headers=world["a"]["headers"]).json()["empty_reason_labels"]
    for row in _rows(world).values():
        assert (row["value"] is None) == (row["empty_reason"] is not None)
        if row["empty_reason"]:
            assert row["empty_reason"] in served


# -- labels are served by the API ------------------------------------------------------


def test_labels_serve_the_panels_wording_and_limits(world):
    body = client.get("/pins/labels", headers=world["a"]["headers"]).json()
    assert [f["field"] for f in body["condition_fields"]] == [
        f["field"] for f in labels.CONDITION_FIELDS
    ]
    assert body["stale_limits_s"] == {
        "weather": 7200,
        "gauge": 21600,
        "water_quality": 2592000,
    }
    assert body["trend_labels"] == {"rising": "Rising", "falling": "Falling", "steady": "Steady"}
    assert body["stale_label"] == "stale"
    assert set(body["condition_group_labels"]) == {f["group"] for f in labels.CONDITION_FIELDS}


def test_every_empty_reason_has_a_user_facing_label():
    assert {r.value for r in EmptyReason} == set(labels.EMPTY_REASON_LABELS)


def test_age_wording():
    assert labels.age_label(None) is None
    assert labels.age_label(30) == "just now"
    assert labels.age_label(12 * 60) == "12 min ago"
    assert labels.age_label(3 * 3600) == "3 h ago"
    assert labels.age_label(41 * 86400) == "41 days ago"


# -- who may read a pin's conditions ------------------------------------------------------


def test_another_users_pin_answers_exactly_like_a_missing_one(world):
    mine = world["a"]["headers"]
    theirs = client.get(f"/pins/{world['b']['pin_id']}/conditions", headers=mine)
    missing = client.get(f"/pins/{MISSING}/conditions", headers=mine)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content


def test_each_user_can_read_only_their_own_pins_panel(world):
    for me, other in (("a", "b"), ("b", "a")):
        assert (
            client.get(
                f"/pins/{world[me]['pin_id']}/conditions", headers=world[me]["headers"]
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/pins/{world[other]['pin_id']}/conditions", headers=world[me]["headers"]
            ).status_code
            == 404
        )


def test_another_users_pin_triggers_no_live_lookup(world, monkeypatch):
    """A guessed id must not spend a call on someone else's coordinates."""
    calls = []
    monkeypatch.setattr("src.services.stream_gauge.nearest_gauge", lambda *a, **k: calls.append(a))
    client.get(f"/pins/{world['b']['pin_id']}/conditions", headers=world["a"]["headers"])
    assert calls == []


def test_the_route_needs_a_token(world):
    assert client.get(f"/pins/{world['a']['pin_id']}/conditions").status_code in (401, 403)


def test_conditions_do_not_change_anything_in_the_pin_tables(world):
    db = world["db"]
    before = [
        db.execute(f"SELECT * FROM {t}").fetchall() for t in ("pins", "pin_links", "sessions")
    ]
    _rows(world)
    after = [db.execute(f"SELECT * FROM {t}").fetchall() for t in ("pins", "pin_links", "sessions")]
    assert before == after


# -- the recorded example the UI is built against ------------------------------------------


def test_the_documented_example_is_valid_and_complete():
    """tests/fixtures/pin_conditions_response.json is a real response from the live
    sources; the UI is built against it, so it must stay valid against the model."""
    example = json.loads(FIXTURE.read_text())
    assert [r["field"] for r in example] == [f["field"] for f in labels.CONDITION_FIELDS]
    for row in example:
        assert set(row) == ROW_KEYS
        PinConditionRow.model_validate(row)
