"""GET /pins/today: the user's own pins, in the order they pick, each saying why.

Weather and the gauge are canned; every database is temporary. The cases that
matter most: every row of every sort states a reason, only the match sort looks
up conditions, and nothing another angler logs can move or reword a row.
"""

from datetime import date, timedelta

import pytest

from src.models.pin import TodayResponse
from src.services.pins import labels
from src.storage.database import get_db
from tests.pin_world import (
    EAST,
    FAR,
    NORTH,
    SPOT,
    Angler,
    Canned,
    build_today_world,
    client,
    gauge_snapshot,
    isolate,
    weather_trip,
)


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


@pytest.fixture()
def world(alice):
    return build_today_world(alice)


def _today(angler: Angler, sort: str, **params) -> list[dict]:
    response = angler.get("/pins/today", sort=sort, **params)
    assert response.status_code == 200, response.text
    body = response.json()
    TodayResponse.model_validate(body)
    assert body["sort"] == sort and body["sort_label"] == labels.TODAY_SORT_LABELS[sort]
    return body["rows"]


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


@pytest.mark.parametrize("sort", sorted(labels.TODAY_SORT_LABELS))
def test_every_row_of_every_sort_states_a_reason_and_a_rank(alice, world, canned, sort):
    rows = _today(alice, sort, lat=SPOT[0], lng=SPOT[1])
    assert len(rows) == 5
    assert [r["rank"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(r["reason"].strip() for r in rows)


def test_the_default_sort_is_the_match(alice, world, canned):
    body = alice.get("/pins/today").json()
    assert body["sort"] == "match"


# -- match ------------------------------------------------------------------------


def test_match_ranks_similar_then_different_then_unmatched(alice, world, canned):
    rows = _today(alice, "match")
    assert _names(rows)[:2] == ["Bronte pool", "Credit mouth"]
    assert rows[0]["match"]["status"] == "similar"
    assert rows[1]["match"]["status"] == "different"
    # Pins with fewer than three rated trips sit below, by name.
    assert _names(rows)[2:] == ["Humber", "Old haunt", "Wishlist"]
    assert all(r["match"]["status"] == "hidden" for r in rows[2:])


def test_a_match_row_reason_is_the_matchs_headline(alice, world, canned):
    rows = {r["name"]: r for r in _today(alice, "match")}
    assert rows["Bronte pool"]["reason"] == rows["Bronte pool"]["match"]["headline"]
    assert rows["Bronte pool"]["reason"].startswith("Today looks like your best trips here: ")
    assert rows["Humber"]["reason"] == "No match yet: 2 more rated trips here would show one."
    assert rows["Wishlist"]["reason"] == "No match yet: 3 more rated trips here would show one."


def test_match_only_looks_up_conditions_for_pins_that_can_be_matched(alice, world, canned):
    _today(alice, "match")
    # Two pins can be matched; each is one weather and one gauge lookup.
    assert canned.lookups == 4


@pytest.mark.parametrize("sort", ["distance", "last_visited", "favourites"])
def test_the_other_sorts_make_no_outside_call_and_carry_no_match(alice, world, canned, sort):
    rows = _today(alice, sort, lat=SPOT[0], lng=SPOT[1])
    assert canned.lookups == 0
    assert all(r["match"] is None for r in rows)


def test_stale_conditions_leave_pins_unranked_by_match_but_still_listed(alice, world, canned):
    canned.weather_age = timedelta(hours=5)
    canned.gauge_age = timedelta(hours=9)
    rows = _today(alice, "match")
    assert len(rows) == 5
    assert rows[0]["match"]["status"] == "not_enough_readings"
    assert "Too few of today's readings" in rows[0]["reason"]


# -- distance ---------------------------------------------------------------------


def test_distance_orders_nearest_first_and_says_how_far(alice, world, canned):
    rows = _today(alice, "distance", lat=SPOT[0], lng=SPOT[1])
    assert _names(rows) == ["Bronte pool", "Wishlist", "Credit mouth", "Old haunt", "Humber"]
    assert rows[0]["reason"] == "0 m from here"
    assert rows[1]["reason"] == "4.0 km from here"
    assert rows[0]["distance_km"] == 0.0 and rows[1]["distance_km"] == pytest.approx(4.04, abs=0.05)


def test_distance_from_another_place_reorders(alice, world, canned):
    rows = _today(alice, "distance", lat=FAR[0], lng=FAR[1])
    assert _names(rows)[0] == "Humber"


def test_distance_needs_the_anglers_position(alice, world, canned):
    assert alice.get("/pins/today", sort="distance").status_code == 400
    assert alice.get("/pins/today", sort="distance", lat=43.4).status_code == 400
    assert alice.get("/pins/today", sort="match", lng=-79.7).status_code == 400


def test_a_position_only_adds_distance_to_the_other_sorts(alice, world, canned):
    without = _today(alice, "favourites")
    assert all(r["distance_km"] is None for r in without)
    with_position = _today(alice, "favourites", lat=SPOT[0], lng=SPOT[1])
    assert all(r["distance_km"] is not None for r in with_position)
    assert _names(with_position) == _names(without)


@pytest.mark.parametrize(
    "params", [{"lat": 91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": "x", "lng": 0}]
)
def test_an_impossible_position_is_rejected(alice, world, canned, params):
    assert alice.get("/pins/today", sort="distance", **params).status_code == 422


def test_an_unknown_sort_is_rejected(alice, world, canned):
    assert alice.get("/pins/today", sort="best").status_code == 422


# -- last visited -----------------------------------------------------------------


def test_last_visited_orders_newest_first_and_says_when(alice, world, canned):
    rows = _today(alice, "last_visited")
    assert _names(rows)[:3] == ["Humber", "Credit mouth", "Bronte pool"]
    days = (date.today() - date(2026, 10, 10)).days
    expected = "Last fished today" if days == 0 else f"Last fished {days} days ago (2026-10-10)"
    if days == 1:
        expected = "Last fished yesterday"
    assert rows[0]["reason"] == expected
    assert rows[0]["last_visit"] == "2026-10-10"


def test_pins_never_fished_come_last_and_say_so(alice, world, canned):
    rows = _today(alice, "last_visited")
    assert _names(rows)[3:] == ["Old haunt", "Wishlist"]
    assert {r["reason"] for r in rows[3:]} == {labels.TODAY_NEVER_VISITED_REASON}


def test_a_recalled_visit_is_not_a_last_visit(alice, world, canned):
    old = next(r for r in _today(alice, "last_visited") if r["name"] == "Old haunt")
    assert old["last_visit"] is None and old["state"] == "visited"  # visited, but never logged


# -- favourites -------------------------------------------------------------------


def test_favourites_come_first_then_the_most_recently_fished(alice, world, canned):
    rows = _today(alice, "favourites")
    assert _names(rows) == ["Credit mouth", "Humber", "Bronte pool", "Old haunt", "Wishlist"]
    assert rows[0]["reason"] == labels.TODAY_FAVOURITE_REASON
    assert {r["reason"] for r in rows[1:]} == {labels.TODAY_NOT_FAVOURITE_REASON}


# -- the row ----------------------------------------------------------------------


def test_a_row_carries_the_pin_it_stands_for(alice, world, canned):
    row = next(r for r in _today(alice, "favourites") if r["name"] == "Credit mouth")
    assert row["pin_id"] == world["credit"]
    assert (row["lat"], row["lng"]) == NORTH
    assert row["jurisdiction"] == "CA-ON"
    assert row["is_favourite"] is True and row["state"] == "visited" and row["visits"] == 3


def test_a_user_with_no_pins_gets_an_empty_list(bob, canned):
    for sort in labels.TODAY_SORT_LABELS:
        assert _today(bob, sort, lat=SPOT[0], lng=SPOT[1]) == []


def test_the_route_requires_a_user(world):
    assert client.get("/pins/today").status_code in (401, 403)


# -- no other user's data can reach the result -------------------------------------


def _everything(angler: Angler) -> dict:
    return {
        sort: angler.get("/pins/today", sort=sort, lat=SPOT[0], lng=SPOT[1]).json()
        for sort in labels.TODAY_SORT_LABELS
    }


def test_another_users_pins_are_never_listed(alice, bob, world, canned):
    bob.pin("Bobs secret run", SPOT, favourite=True)
    for sort in labels.TODAY_SORT_LABELS:
        assert "Bobs secret run" not in _names(_today(alice, sort, lat=SPOT[0], lng=SPOT[1]))
    assert _names(_today(bob, "favourites")) == ["Bobs secret run"]


def test_another_users_trips_and_pins_change_no_row_of_any_sort(alice, bob, world, canned):
    before = _everything(alice)

    # Bob has pins on top of Alice's, and fishes all of them hard, recently, in
    # the same kind of conditions, so any leak would reorder or reword Alice's rows.
    for point in (SPOT, NORTH, FAR, EAST):
        bob.pin("bob " + str(point), point, favourite=True)
    for point in (SPOT, NORTH, FAR, EAST):
        for day in (1, 8, 15, 22):
            bob.trip(
                f"2026-10-{day:02d}",
                5,
                point=point,
                conditions=weather_trip(10.0) | gauge_snapshot(18.3),
            )
    assert _everything(alice) == before


def test_the_conditions_looked_up_for_a_match_belong_to_the_askers_pins_only(
    alice, bob, world, canned
):
    bob.pin("Bob's", (SPOT[0] - 0.5, SPOT[1]))
    canned.lookups = 0
    _today(bob, "match")
    assert canned.lookups == 0  # Bob has no rated trips, so nothing of Alice's was fetched
