"""The match indicator: today's conditions against the user's own best trips at a pin.

Two layers. `evaluate` is pure (rated trips and today's readings in, a verdict
out), so each rule is tested on its own. The route tests then prove the parts
that only a database can: whose trips count, what a hidden match looks up, and
that another angler's data cannot move the answer. Weather and the gauge are
canned and every database is temporary.
"""

import json
from datetime import timedelta

import pytest

from src.models.pin import PinMatch
from src.services.pins import labels
from src.services.pins.match import Reading, best_trips, evaluate
from src.storage.database import get_db
from tests.pin_world import (
    GAUGE_NAME,
    SPOT,
    Angler,
    Canned,
    client,
    gauge_snapshot,
    isolate,
    weather_trip,
)

# -- evaluate(): the rules, without a database ------------------------------------


def _trip(rating: int, when: str | None = "2026-04-12", **columns) -> dict:
    base = {
        "session_id": id(columns) + rating,
        "rating": rating,
        "date": when,
        "air_temp_c": None,
        "wind_speed_kmh": None,
        "pressure_hpa": None,
        "flow_cms": None,
        "level_m": None,
        "water_temp_c": None,
        "do_mgl": None,
        "ph": None,
        "gauge_station_id": None,
        "gauge_station_name": None,
        "pwqmn_sampled_at": None,
    }
    return {**base, **columns}


def _today(**fields) -> dict[str, Reading]:
    return {k: v if isinstance(v, Reading) else Reading(value=v) for k, v in fields.items()}


def _april_trips() -> list[dict]:
    return [
        _trip(5, "2026-04-05", air_temp_c=8.0, wind_speed_kmh=10.0),
        _trip(4, "2026-04-19", air_temp_c=10.0, wind_speed_kmh=12.0),
        _trip(4, "2026-04-26", air_temp_c=12.0, wind_speed_kmh=20.0),
    ]


def test_the_example_from_the_spec_reads_in_plain_text():
    match = evaluate(7, _april_trips(), _today(air_temp_c=10.0, wind_speed_kmh=11.0))
    assert match.status == "similar"
    assert match.reason.startswith("Your 3 best trips here were in April. ")
    assert "Air temperature: 8–12°C on those trips, 10°C now (inside that range)." in match.reason
    assert "Wind: 10–20 km/h on those trips, 11 km/h now (inside that range)." in match.reason
    assert match.best_months == ["April"]
    assert match.headline == (
        "Today looks like your best trips here: 2 of 2 conditions compared are in their range."
    )


def test_the_match_never_calls_flow_normal_or_promises_a_bite():
    trips = [
        _trip(5, flow_cms=1.0, gauge_station_name=GAUGE_NAME),
        _trip(4, flow_cms=2.0, gauge_station_name=GAUGE_NAME),
        _trip(4, flow_cms=1.5, gauge_station_name=GAUGE_NAME),
    ]
    match = evaluate(
        1, trips, _today(flow_cms=Reading(value=1.4, station=GAUGE_NAME), air_temp_c=None)
    )
    text = json.dumps(match.model_dump()).lower()
    assert "normal" not in text and "bite" not in text and "likely" not in text


def test_fewer_rated_trips_than_the_minimum_hides_the_match():
    for count in range(labels.MIN_RATED_TRIPS_FOR_MATCH):
        match = evaluate(1, [_trip(5, air_temp_c=10.0)] * count, _today(air_temp_c=10.0))
        assert match.status == "hidden" and match.shown is False
        assert match.rated_trips == count
        assert match.comparisons == [] and match.left_out == [] and match.score is None
        assert match.min_rated_trips == labels.MIN_RATED_TRIPS_FOR_MATCH


def test_the_minimum_itself_shows_the_match():
    trips = [_trip(5, air_temp_c=8.0), _trip(5, air_temp_c=10.0), _trip(4, air_temp_c=9.0)]
    assert len(trips) == labels.MIN_RATED_TRIPS_FOR_MATCH
    assert evaluate(1, trips, _today(air_temp_c=9.0)).shown is True


def test_only_the_best_trips_set_the_range():
    trips = [
        _trip(5, air_temp_c=8.0, wind_speed_kmh=9.0),
        _trip(5, air_temp_c=10.0, wind_speed_kmh=11.0),
        _trip(4, air_temp_c=12.0, wind_speed_kmh=13.0),
        _trip(2, air_temp_c=30.0, wind_speed_kmh=40.0),
        _trip(1, air_temp_c=-5.0, wind_speed_kmh=0.0),
    ]
    match = evaluate(1, trips, _today(air_temp_c=31.0, wind_speed_kmh=10.0))
    air = next(c for c in match.comparisons if c.field == "air_temp_c")
    assert (air.low, air.high, air.trips) == (8.0, 12.0, 3)
    assert air.position == "above"  # the slow trip at 30 °C did not widen the range
    assert match.best_trips == 3


def test_trips_tied_at_the_cutoff_are_all_kept():
    trips = [_trip(5), _trip(4), _trip(4), _trip(4), _trip(3)]
    assert sorted(t["rating"] for t in best_trips(trips)) == [4, 4, 4, 5]


def test_a_trip_below_the_minimum_best_rating_is_never_a_best_trip():
    assert best_trips([_trip(2), _trip(1), _trip(2)]) == []


def test_without_enough_good_trips_there_is_nothing_to_compare_against():
    trips = [_trip(5, air_temp_c=8.0), _trip(2, air_temp_c=10.0), _trip(1, air_temp_c=12.0)]
    match = evaluate(1, trips, _today(air_temp_c=10.0))
    assert match.status == "few_good_trips" and match.shown is True
    assert match.comparisons == []
    assert "Only 1 of your 3 rated trips here were rated decent or better" in match.headline


def test_a_stale_reading_is_left_out_not_compared():
    trips = [_trip(5, air_temp_c=8.0), _trip(5, air_temp_c=10.0), _trip(4, air_temp_c=12.0)]
    trips = [{**t, "wind_speed_kmh": 10.0} for t in trips]
    match = evaluate(
        1, trips, _today(air_temp_c=Reading(value=10.0, stale=True), wind_speed_kmh=10.0)
    )
    assert [c.field for c in match.comparisons] == ["wind_speed_kmh"]
    stale = next(x for x in match.left_out if x.field == "air_temp_c")
    assert stale.reason == "stale" and stale.text == labels.MATCH_LEFT_OUT_REASONS["stale"]
    assert "Air temperature (today's reading is out of date)" in match.reason


def test_a_missing_reading_is_left_out_with_the_panels_reason():
    trips = [_trip(5, air_temp_c=8.0), _trip(5, air_temp_c=10.0), _trip(4, air_temp_c=12.0)]
    match = evaluate(
        1, trips, _today(air_temp_c=Reading(value=None, empty_reason="live_lookup_failed"))
    )
    gone = next(x for x in match.left_out if x.field == "air_temp_c")
    assert gone.reason == "unavailable" and gone.empty_reason == "live_lookup_failed"


def test_a_condition_the_best_trips_barely_recorded_is_left_out():
    trips = [_trip(5, air_temp_c=8.0), _trip(5), _trip(4)]  # only one trip has a reading
    match = evaluate(1, trips, _today(air_temp_c=8.0))
    assert match.comparisons == []
    assert {x.reason for x in match.left_out if x.field == "air_temp_c"} == {"too_few_trips"}
    assert match.status == "not_enough_readings"
    assert match.score is None


def test_one_comparable_condition_is_not_enough_to_say_anything():
    trips = [_trip(5, air_temp_c=8.0), _trip(5, air_temp_c=10.0), _trip(4, air_temp_c=12.0)]
    match = evaluate(1, trips, _today(air_temp_c=10.0))
    assert len(match.comparisons) == 1 < labels.MIN_COMPARED_CONDITIONS
    assert match.status == "not_enough_readings"


def test_flow_from_another_gauge_is_not_compared():
    trips = [
        _trip(5, flow_cms=1.0, gauge_station_name="HUMBER RIVER"),
        _trip(5, flow_cms=1.2, gauge_station_name="HUMBER RIVER"),
        _trip(4, flow_cms=1.1, gauge_station_name="HUMBER RIVER"),
    ]
    match = evaluate(1, trips, _today(flow_cms=Reading(value=1.1, station=GAUGE_NAME)))
    assert match.comparisons == []
    assert next(x for x in match.left_out if x.field == "flow_cms").reason == "other_gauge"


def test_flow_from_the_same_gauge_is_compared_by_station_name_or_id():
    by_name = [_trip(5, flow_cms=1.0, gauge_station_name=GAUGE_NAME)] * 2 + [_trip(4)]
    by_id = [_trip(5, flow_cms=1.0, gauge_station_id="02HB001")] * 2 + [_trip(4)]
    today = _today(flow_cms=Reading(value=1.0, station=GAUGE_NAME))
    assert evaluate(1, by_name, today).comparisons[0].field == "flow_cms"
    # A gauge with no name is described by its id, the way the panel names it.
    today_by_id = _today(flow_cms=Reading(value=1.0, station="02HB001"))
    assert evaluate(1, by_id, today_by_id).comparisons[0].field == "flow_cms"


def test_a_water_sample_far_from_the_trip_does_not_describe_it():
    old = [
        _trip(5, "2026-04-10", water_temp_c=6.0, pwqmn_sampled_at="2024-01-05"),
        _trip(5, "2026-04-17", water_temp_c=7.0, pwqmn_sampled_at="2024-01-05"),
        _trip(4, "2026-04-24", water_temp_c=8.0, pwqmn_sampled_at="2024-01-05"),
    ]
    match = evaluate(1, old, _today(water_temp_c=7.0))
    assert next(x for x in match.left_out if x.field == "water_temp_c").reason == "old_samples"

    near = [{**t, "pwqmn_sampled_at": t["date"]} for t in old]
    match = evaluate(1, near, _today(water_temp_c=7.0))
    assert [c.field for c in match.comparisons] == ["water_temp_c"]


@pytest.mark.parametrize(
    ("current", "position"),
    [
        (10.0, "inside"),
        (8.0, "inside"),  # the ends of the range are inside it
        (12.0, "inside"),
        (13.5, "near"),  # outside by less than the 2 °C margin
        (5.0, "below"),
        (20.0, "above"),
    ],
)
def test_where_a_reading_sits_against_the_range(current, position):
    trips = [_trip(5, air_temp_c=8.0), _trip(5, air_temp_c=12.0), _trip(4, air_temp_c=10.0)]
    match = evaluate(1, trips, _today(air_temp_c=current))
    assert match.comparisons[0].position == position


def test_the_status_follows_the_share_of_conditions_in_range():
    trips = [
        _trip(5, air_temp_c=8.0, wind_speed_kmh=5.0, pressure_hpa=1000.0),
        _trip(5, air_temp_c=12.0, wind_speed_kmh=9.0, pressure_hpa=1010.0),
        _trip(4, air_temp_c=10.0, wind_speed_kmh=7.0, pressure_hpa=1005.0),
    ]
    inside = dict(air_temp_c=10.0, wind_speed_kmh=7.0, pressure_hpa=1005.0)
    assert evaluate(1, trips, _today(**inside)).status == "similar"
    one_off = {**inside, "pressure_hpa": 1040.0}
    partly = evaluate(1, trips, _today(**one_off))
    assert partly.status == "partly" and partly.score == pytest.approx(2 / 3, abs=1e-3)
    two_off = {**one_off, "wind_speed_kmh": 40.0}
    different = evaluate(1, trips, _today(**two_off))
    assert different.status == "different" and different.inside == 1


def test_a_single_best_value_reads_as_a_value_not_a_range():
    trips = [_trip(5, air_temp_c=9.0), _trip(5, air_temp_c=9.0), _trip(4, air_temp_c=9.0)]
    assert (
        "Air temperature: 9°C on those trips" in evaluate(1, trips, _today(air_temp_c=9.0)).reason
    )


def test_months_are_listed_once_in_calendar_order():
    trips = [
        _trip(5, "2026-06-01"),
        _trip(5, "2025-04-12"),
        _trip(5, "2024-04-20"),
        _trip(5, "2023-05-02"),
    ]
    match = evaluate(1, trips, _today())
    assert match.best_months == ["April", "May", "June"]
    assert "were in April, May and June." in match.reason


def test_trips_without_a_date_still_count_and_say_no_month():
    trips = [_trip(5, None), _trip(5, None), _trip(4, None)]
    match = evaluate(1, trips, _today())
    assert match.best_months == []
    assert match.reason.startswith("Your 3 best trips here. ")


def test_every_status_and_reason_has_wording():
    assert set(labels.MATCH_STATUS_LABELS) == set(labels.MATCH_HEADLINES)
    left_out = {"stale", "unavailable", "too_few_trips", "other_gauge", "old_samples"}
    assert set(labels.MATCH_LEFT_OUT_REASONS) == left_out
    assert set(labels.MATCH_POSITION_LABELS) == {"inside", "near", "below", "above"}
    fields = {f["field"] for f in labels.CONDITION_FIELDS}
    assert {v["field"] for v in labels.MATCH_VARIABLES} <= fields


# -- GET /pins/{id}/match ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)


@pytest.fixture()
def canned(monkeypatch):
    return Canned().install(monkeypatch)


@pytest.fixture()
def alice():
    return Angler(get_db(), 2, "alice")


@pytest.fixture()
def bob():
    return Angler(get_db(), 3, "bob")


def _april_pin(angler: Angler) -> int:
    """A pin with three rated April trips whose weather and flow are on record."""
    pin_id = angler.pin("Bronte pool")
    for when, rating, air, flow in (
        ("2026-04-05", 5, 8.0, 14.0),
        ("2026-04-19", 4, 10.0, 18.0),
        ("2026-04-26", 4, 12.0, 22.0),
    ):
        angler.trip(when, rating, conditions=weather_trip(air) | gauge_snapshot(flow, level=0.5))
    return pin_id


def _match(angler: Angler, pin_id: int) -> dict:
    response = angler.get(f"/pins/{pin_id}/match")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_pin_with_enough_rated_trips_is_matched_against_today(alice, canned):
    pin_id = _april_pin(alice)
    match = _match(alice, pin_id)
    PinMatch.model_validate(match)
    assert match["label"] == labels.MATCH_INDICATOR_LABEL
    assert match["status"] == "similar" and match["shown"] is True
    fields = {c["field"]: c for c in match["comparisons"]}
    assert set(fields) == {"air_temp_c", "wind_speed_kmh", "pressure_hpa", "flow_cms", "level_m"}
    assert fields["air_temp_c"]["current"] == 10.0 and fields["air_temp_c"]["position"] == "inside"
    assert fields["flow_cms"]["low"] == 14.0 and fields["flow_cms"]["high"] == 22.0
    assert fields["flow_cms"]["current"] == 18.3
    assert "Your 3 best trips here were in April." in match["reason"]


def test_a_hidden_match_shows_only_counts_and_looks_nothing_up(alice, canned):
    pin_id = alice.pin("New pool")
    alice.trip("2026-04-05", 5, conditions=weather_trip(8.0))
    alice.trip("2026-04-19", 4, conditions=weather_trip(10.0))
    alice.trip("2026-04-26", None, conditions=weather_trip(12.0))  # not rated: not counted
    match = _match(alice, pin_id)
    assert match["status"] == "hidden" and match["shown"] is False
    assert match["rated_trips"] == 2 and match["min_rated_trips"] == 3
    assert match["comparisons"] == [] and match["left_out"] == [] and match["score"] is None
    assert match["reason"] == "No match yet: 1 more rated trip here would show one."
    assert canned.lookups == 0


def test_a_pin_with_no_trips_is_hidden(alice, canned):
    match = _match(alice, alice.pin("Empty"))
    assert match["status"] == "hidden" and match["rated_trips"] == 0
    assert "3 more rated trips" in match["reason"]


def test_recalled_visits_never_count_toward_the_minimum_or_the_range(alice, canned):
    pin_id = alice.pin("Old haunt")
    alice.trip("2026-04-05", 5, conditions=weather_trip(8.0))
    alice.trip("2026-04-19", 5, conditions=weather_trip(10.0))
    for _ in range(5):
        alice.recall(pin_id, rating=5)
    match = _match(alice, pin_id)
    assert match["status"] == "hidden" and match["rated_trips"] == 2


def test_a_trip_with_two_stops_at_the_pin_counts_once(alice, canned):
    pin_id = alice.pin("Long run")
    alice.trip("2026-04-05", 5, stops=2, conditions=weather_trip(8.0))
    alice.trip("2026-04-19", 5, conditions=weather_trip(10.0))
    assert _match(alice, pin_id)["rated_trips"] == 2


def test_trips_at_a_different_pin_do_not_count(alice, canned):
    pin_id = alice.pin("Here")
    far = (SPOT[0] + 0.5, SPOT[1])
    alice.pin("There", far)
    for when in ("2026-04-05", "2026-04-12", "2026-04-19"):
        alice.trip(when, 5, point=far, conditions=weather_trip(10.0))
    assert _match(alice, pin_id)["status"] == "hidden"


def test_old_trips_without_stored_conditions_still_count_as_rated(alice, canned):
    pin_id = alice.pin("Old log")
    alice.trip("2026-04-05", 5)  # no session_conditions row at all
    alice.trip("2026-04-12", 5, conditions=weather_trip(8.0))
    alice.trip("2026-04-19", 4, conditions=weather_trip(10.0))
    match = _match(alice, pin_id)
    assert match["rated_trips"] == 3 and match["best_trips"] == 3
    air = next(c for c in match["comparisons"] if c["field"] == "air_temp_c")
    assert air["trips"] == 2


def test_a_stale_weather_reading_is_left_out_and_the_gauge_still_compares(alice, canned):
    pin_id = _april_pin(alice)
    canned.weather_age = timedelta(hours=3)
    match = _match(alice, pin_id)
    assert {c["field"] for c in match["comparisons"]} == {"flow_cms", "level_m"}
    stale = {x["field"] for x in match["left_out"] if x["reason"] == "stale"}
    assert {"air_temp_c", "wind_speed_kmh", "pressure_hpa"} <= stale


def test_a_stale_gauge_reading_is_left_out(alice, canned):
    pin_id = _april_pin(alice)
    canned.gauge_age = timedelta(hours=7)
    match = _match(alice, pin_id)
    assert {c["field"] for c in match["comparisons"]} == {
        "air_temp_c",
        "wind_speed_kmh",
        "pressure_hpa",
    }
    assert {x["reason"] for x in match["left_out"] if x["field"] == "flow_cms"} == {"stale"}


def test_when_both_live_sources_are_stale_nothing_is_compared(alice, canned):
    pin_id = _april_pin(alice)
    canned.weather_age = timedelta(hours=3)
    canned.gauge_age = timedelta(hours=7)
    match = _match(alice, pin_id)
    assert match["comparisons"] == [] and match["status"] == "not_enough_readings"


def test_the_pins_own_gauge_is_the_one_compared(alice, canned):
    pin_id = _april_pin(alice)
    canned.station = "SOME OTHER CREEK"
    match = _match(alice, pin_id)
    assert "flow_cms" not in {c["field"] for c in match["comparisons"]}
    assert next(x for x in match["left_out"] if x["field"] == "flow_cms")["reason"] == "other_gauge"


def test_a_pin_with_only_slow_trips_has_no_match_to_make(alice, canned):
    pin_id = alice.pin("Skunk water")
    for when, rating in (("2026-04-05", 1), ("2026-04-12", 2), ("2026-04-19", 2)):
        alice.trip(when, rating, conditions=weather_trip(10.0))
    match = _match(alice, pin_id)
    assert match["status"] == "few_good_trips" and match["comparisons"] == []
    assert canned.lookups == 0  # nothing to compare against, so nothing is looked up


def test_the_route_requires_a_user(alice, canned):
    pin_id = _april_pin(alice)
    assert client.get(f"/pins/{pin_id}/match").status_code in (401, 403)


# -- no other user's data can reach the result -------------------------------------


def test_another_users_rated_trips_at_the_same_place_do_not_change_the_match(alice, bob, canned):
    pin_id = _april_pin(alice)
    before = _match(alice, pin_id)

    # Bob fishes the very same water, rates it highly, in very different conditions,
    # with his own pin on it and his own matching gauge snapshots.
    bob.pin("Same pool")
    for when in ("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"):
        bob.trip(when, 5, conditions=weather_trip(-20.0, wind=60.0) | gauge_snapshot(900.0))
    assert _match(alice, pin_id) == before
    assert before["rated_trips"] == 3 and before["best_months"] == ["April"]


def test_another_users_trips_never_make_my_hidden_pin_visible(alice, bob, canned):
    mine = alice.pin("Mine")
    bob.pin("Bobs")
    for when in ("2026-04-05", "2026-04-12", "2026-04-19"):
        bob.trip(when, 5, conditions=weather_trip(10.0))
    match = _match(alice, mine)
    assert match["status"] == "hidden" and match["rated_trips"] == 0


def test_a_user_can_not_read_a_match_for_someone_elses_pin(alice, bob, canned):
    pin_id = _april_pin(alice)
    theirs = client.get(f"/pins/{pin_id}/match", headers=bob.headers)
    missing = client.get("/pins/999999/match", headers=bob.headers)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content
    assert canned.lookups == 0  # Bob's attempt made no outside call on Alice's coordinates


def test_the_match_reads_trips_only_through_the_owning_user(alice, bob, canned):
    """A condition row stamped with the wrong owner must not matter: the join goes by session."""
    pin_id = _april_pin(alice)
    before = _match(alice, pin_id)
    with alice.db.conn:
        alice.db.execute("UPDATE session_conditions SET user_id = 3")
    assert _match(alice, pin_id) == before


# -- the catch data stays queryable by species for a later filter -------------------


def test_catches_can_be_queried_by_user_and_species():
    db = get_db()
    indexes = {row[1]: row for row in db.execute("PRAGMA index_list(catches)").fetchall()}
    assert "idx_catches_user_species" in indexes
    columns = [r[2] for r in db.execute("PRAGMA index_info(idx_catches_user_species)").fetchall()]
    assert columns == ["user_id", "species"]


def test_labels_serve_the_match_settings_and_wording():
    payload = client.get("/pins/labels", headers=Angler(get_db(), 2, "alice").headers).json()
    assert payload["match_indicator_label"] == labels.MATCH_INDICATOR_LABEL
    assert payload["min_rated_trips_for_match"] == labels.MIN_RATED_TRIPS_FOR_MATCH
    match = payload["match"]
    assert match["status_labels"] == labels.MATCH_STATUS_LABELS
    assert match["position_labels"] == labels.MATCH_POSITION_LABELS
    assert match["left_out_reasons"] == labels.MATCH_LEFT_OUT_REASONS
    assert payload["today_sort_labels"] == labels.TODAY_SORT_LABELS
