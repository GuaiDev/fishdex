"""Personal-map pins: linking, state, rating, facts, merge, delete, recalled.

No model calls and no network: post-log enrichment is stubbed. Every
database is temporary.
"""

import pytest

from src.services import pins as pin_service
from src.services.context import species_history, user_layer
from src.services.fishdex import get_fishdex_data
from src.services.pins.labels import LINK_RADIUS_M
from src.services.trip_logger import log_session
from src.storage import pins as store
from src.storage.catches import get_personal_best
from src.storage.database import get_db

HOME = (43.5000, -79.7000)
# ~100 m north of HOME: inside the link radius.
NEAR = (43.5009, -79.7000)
# ~1.1 km north of HOME: outside it.
FAR = (43.5100, -79.7000)


@pytest.fixture(autouse=True)
def _no_enrichment(monkeypatch):
    """log_session's model call and weather lookup are not under test here."""
    import src.services.trip_enrichment as enrichment
    import src.services.trip_enrichment_conditions as conditions

    monkeypatch.setattr(enrichment, "enrich_session", lambda *a, **k: {})
    monkeypatch.setattr(conditions, "enrich_session_conditions", lambda *a, **k: None)


@pytest.fixture()
def db(tmp_path):
    return get_db(tmp_path / "test.db")


def _stop(point=None, photo=None, species=None, name="Bronte Creek") -> dict:
    stop = {"location_text": name, "location_name": name, "species_caught": species or []}
    if point:
        stop["lat"], stop["lng"] = point
    if photo:
        stop["photo_lat"], stop["photo_lng"] = photo
    return stop


def _trip(db, *stops, user_id=1, date="2026-06-10", catches=None, **kw) -> dict:
    parsed = {"date": date, "stops": list(stops)}
    return log_session(parsed, db, user_id=user_id, structured_catches=catches, **kw)


def _pin(db, point=HOME, user_id=1, name="Home pool", **kw) -> int:
    return pin_service.create_pin(db, user_id, name, point[0], point[1], **kw)["pin_id"]


# ── linking on log ───────────────────────────────────────────────────────────


def test_no_pin_in_range_links_nothing(db):
    _pin(db, FAR)
    result = _trip(db, _stop(HOME))
    (entry,) = result["pin_links"]
    assert entry["link"] is None and entry["candidates"] == []


def test_exactly_one_pin_in_range_links_as_inferred(db):
    pin_id = _pin(db, HOME)
    result = _trip(db, _stop(NEAR))
    (entry,) = result["pin_links"]
    assert entry["link"]["pin_id"] == pin_id
    assert entry["link"]["kind"] == "inferred"
    assert 0 < entry["link"]["distance_m"] <= LINK_RADIUS_M


def test_two_pins_in_range_offer_candidates_and_link_nothing(db):
    a = _pin(db, HOME, name="A")
    b = _pin(db, NEAR, name="B")
    result = _trip(db, _stop((43.50045, -79.7000)))
    (entry,) = result["pin_links"]
    assert entry["link"] is None
    assert {c["pin_id"] for c in entry["candidates"]} == {a, b}
    assert store.link_for_stop(db, 1, entry["stop_id"]) is None


def test_photo_coordinates_win_over_parsed_ones(db):
    pin_id = _pin(db, HOME)
    result = _trip(db, _stop(point=FAR, photo=NEAR))
    assert result["pin_links"][0]["link"]["pin_id"] == pin_id


def test_stop_without_location_is_reported_and_never_linked(db):
    _pin(db, HOME)
    (entry,) = _trip(db, _stop())["pin_links"]
    assert entry["lat"] is None and entry["link"] is None and entry["pin_prompt"] is False


def test_each_stop_of_a_trip_links_separately(db):
    a = _pin(db, HOME, name="A")
    b = _pin(db, FAR, name="B")
    result = _trip(db, _stop(HOME), _stop(FAR))
    assert [e["link"]["pin_id"] for e in result["pin_links"]] == [a, b]


def test_inferred_link_can_be_undone_and_the_trip_stays(db):
    pin_id = _pin(db, HOME)
    result = _trip(db, _stop(HOME))
    stop_id = result["pin_links"][0]["stop_id"]
    assert store.unlink_stop(db, 1, pin_id, stop_id)
    assert store.link_for_stop(db, 1, stop_id) is None
    assert store.get_stop(db, 1, stop_id) is not None
    assert pin_service.pin_detail(db, 1, pin_id)["pin"]["state"] == "saved"


def test_choosing_a_candidate_makes_a_confirmed_link(db):
    a = _pin(db, HOME, name="A")
    _pin(db, NEAR, name="B")
    (entry,) = _trip(db, _stop((43.50045, -79.7000)))["pin_links"]
    link = store.link_stop(db, 1, a, entry["stop_id"], "confirmed")
    assert link["pin_id"] == a and link["link_kind"] == "confirmed"


def test_relinking_a_stop_moves_it_rather_than_linking_twice(db):
    a = _pin(db, HOME, name="A")
    b = _pin(db, FAR, name="B")
    stop_id = _trip(db, _stop(HOME))["pin_links"][0]["stop_id"]
    store.link_stop(db, 1, b, stop_id, "confirmed")
    assert store.link_for_stop(db, 1, stop_id)["pin_id"] == b
    assert pin_service.pin_detail(db, 1, a)["facts"]["visits"] == 0


# ── "Pin this spot" ──────────────────────────────────────────────────────────


def test_logged_stops_are_never_auto_pinned(db):
    _trip(db, _stop(HOME))
    assert store.list_pins(db, 1) == []


def test_second_stop_in_range_of_an_unpinned_one_prompts_to_pin(db):
    first = _trip(db, _stop(HOME))
    assert first["pin_links"][0]["pin_prompt"] is False
    second = _trip(db, _stop(NEAR))
    assert second["pin_links"][0]["pin_prompt"] is True


def test_pin_this_spot_links_the_stop_confirmed_and_earlier_visits_inferred(db):
    earlier = _trip(db, _stop(HOME))["pin_links"][0]["stop_id"]
    now = _trip(db, _stop(NEAR))["pin_links"][0]["stop_id"]
    created = pin_service.create_pin(db, 1, "Bend", *NEAR, from_stop_id=now)
    assert store.link_for_stop(db, 1, now)["link_kind"] == "confirmed"
    assert store.link_for_stop(db, 1, earlier)["link_kind"] == "inferred"
    assert created["inferred_stop_ids"] == [earlier]


def test_new_pin_does_not_take_stops_another_pin_also_covers(db):
    _pin(db, HOME, name="A")
    (entry,) = _trip(db, _stop((43.50045, -79.7000)))["pin_links"]  # A in range
    store.unlink_stop(db, 1, entry["link"]["pin_id"], entry["stop_id"])
    _pin(db, NEAR, name="B")  # now two pins cover it
    assert store.link_for_stop(db, 1, entry["stop_id"]) is None


def test_pin_carries_a_jurisdiction(db):
    pin_id = _pin(db, HOME)
    assert store.get_pin(db, 1, pin_id)["jurisdiction"] == "CA-ON"


# ── state ────────────────────────────────────────────────────────────────────


def test_state_is_saved_until_something_links(db):
    pin_id = _pin(db, HOME)
    assert pin_service.pin_detail(db, 1, pin_id)["pin"]["state"] == "saved"
    _trip(db, _stop(HOME))
    assert pin_service.pin_detail(db, 1, pin_id)["pin"]["state"] == "visited"


def test_recalled_only_pin_is_visited_and_marked_recalled(db):
    pin_id = _pin(db, HOME)
    store.add_recalled_visit(db, 1, pin_id, "summer 2019", ["brook trout"], 4)
    detail = pin_service.pin_detail(db, 1, pin_id)
    assert detail["pin"]["state"] == "visited"
    assert detail["pin"]["has_recalled"] is True
    assert detail["recalled"][0]["provenance"] == "recalled"
    (row,) = pin_service.map_payload(db, 1)["pins"]
    assert row["state"] == "visited" and row["has_recalled"] is True


# ── recalled history stays out of every statistic ────────────────────────────


def test_recalled_history_counts_toward_no_statistic(db):
    pin_id = _pin(db, HOME)
    store.add_recalled_visit(db, 1, pin_id, "2019", ["brook trout"], 5)

    detail = pin_service.pin_detail(db, 1, pin_id)
    assert detail["facts"]["visits"] == 0
    assert detail["facts"]["fish_logged"] == 0
    assert detail["facts"]["species"] == []
    assert detail["facts"]["last_visit"] is None
    assert detail["trips"] == []

    layer = user_layer(db, user_id=1, force=True)
    assert layer.total_sessions == 0 and "brook trout" not in layer.species_logged
    assert species_history(db, "brook trout", user_id=1).caught_stops == 0
    fishdex = get_fishdex_data(db, user_id=1)
    assert fishdex["caughtSpecies"] == []
    assert get_personal_best(db, 1, "brook trout") is None


def test_recalled_dates_never_become_last_visit(db):
    pin_id = _pin(db, HOME)
    _trip(db, _stop(HOME), date="2024-05-01")
    store.add_recalled_visit(db, 1, pin_id, "2026-09-30", [], None)
    assert pin_service.pin_detail(db, 1, pin_id)["facts"]["last_visit"] == "2024-05-01"
    (row,) = pin_service.map_payload(db, 1)["pins"]
    assert row["last_visit"] == "2024-05-01"


# ── rating ───────────────────────────────────────────────────────────────────


def _trip_rating(db, pin_id, session_id):
    trips = pin_service.pin_detail(db, 1, pin_id)["trips"]
    return next(t["rating"] for t in trips if t["session_id"] == session_id)


def test_unrated_trip_is_null_not_low(db):
    pin_id = _pin(db, HOME)
    session_id = _trip(db, _stop(HOME))["session_id"]
    assert _trip_rating(db, pin_id, session_id) is None
    assert store.get_session(db, 1, session_id)["rating"] is None


def test_single_pin_trip_rating_is_the_pin_rating(db):
    pin_id = _pin(db, HOME)
    session_id = _trip(db, _stop(HOME))["session_id"]
    store.update_session(db, 1, session_id, {"rating": 4})
    assert _trip_rating(db, pin_id, session_id) == 4


def test_trip_rated_before_linking_passes_its_rating_to_its_only_pin(db):
    session_id = _trip(db, _stop(HOME))["session_id"]
    store.update_session(db, 1, session_id, {"rating": 2})
    pin_id = _pin(db, HOME)  # back-links the stop
    assert _trip_rating(db, pin_id, session_id) == 2


def test_multi_pin_trip_is_rated_per_pin_and_unanswered_stays_null(db):
    a = _pin(db, HOME, name="A")
    b = _pin(db, FAR, name="B")
    session_id = _trip(db, _stop(HOME), _stop(FAR))["session_id"]
    store.update_session(db, 1, session_id, {"rating": 5})
    assert _trip_rating(db, a, session_id) is None
    assert _trip_rating(db, b, session_id) is None
    assert store.set_trip_pin_rating(db, 1, session_id, a, 5)
    assert _trip_rating(db, a, session_id) == 5
    assert _trip_rating(db, b, session_id) is None


def test_rating_a_pin_the_trip_never_touched_fails(db):
    _pin(db, HOME, name="A")
    other = _pin(db, FAR, name="B")
    session_id = _trip(db, _stop(HOME))["session_id"]
    assert store.set_trip_pin_rating(db, 1, session_id, other, 3) is False


def test_pin_never_shows_an_averaged_rating(db):
    pin_id = _pin(db, HOME)
    for rating in (1, 5):
        sid = _trip(db, _stop(HOME))["session_id"]
        store.update_session(db, 1, sid, {"rating": rating})
    detail = pin_service.pin_detail(db, 1, pin_id)
    assert "rating" not in detail["facts"] and "rating" not in detail["pin"]
    assert sorted(t["rating"] for t in detail["trips"]) == [1, 5]


# ── facts ────────────────────────────────────────────────────────────────────


def test_facts_count_a_missing_count_as_one_fish(db):
    pin_id = _pin(db, HOME)
    _trip(
        db,
        _stop(HOME, species=["rock bass"]),  # NL-parsed: count NULL
        catches=[
            {"species": "creek chub", "count": 3},
            {"species": "smallmouth bass", "count": None, "biggest_size_cm": 31.0},
        ],
    )
    facts = pin_service.pin_detail(db, 1, pin_id)["facts"]
    assert facts["fish_logged"] == 3 + 1 + 1
    assert facts["biggest_fish"]["size_cm"] == 31.0
    # The NL-parsed rock bass is still unconfirmed, so it is not a species yet.
    assert facts["species"] == ["creek chub", "smallmouth bass"]


def test_stored_count_stays_null_when_missing(db):
    _pin(db, HOME)
    sid = _trip(db, _stop(HOME), catches=[{"species": "creek chub", "count": None}])["session_id"]
    (count,) = db.execute("SELECT count FROM catches WHERE session_id = ?", [sid]).fetchone()
    assert count is None


def test_fish_per_hour_only_from_timed_trips_spent_at_the_pin(db):
    pin_id = _pin(db, HOME)
    _trip(db, _stop(HOME))  # untimed: excluded
    _trip(
        db,
        _stop(HOME),
        catches=[{"species": "creek chub", "count": 4}],
        started_at="2026-06-10T08:00:00+00:00",
        ended_at="2026-06-10T10:00:00+00:00",
    )
    _trip(  # timed but also fished elsewhere: hours cannot be split
        db,
        _stop(HOME),
        _stop(FAR),
        catches=[{"species": "creek chub", "count": 9}],
        started_at="2026-06-11T08:00:00+00:00",
        ended_at="2026-06-11T09:00:00+00:00",
    )
    facts = pin_service.pin_detail(db, 1, pin_id)["facts"]
    assert facts["fish_per_hour"] == 2.0
    assert facts["fish_per_hour_trips"] == 1


def test_fish_per_hour_is_absent_without_timed_trips(db):
    pin_id = _pin(db, HOME)
    _trip(db, _stop(HOME), catches=[{"species": "creek chub", "count": 4}])
    assert pin_service.pin_detail(db, 1, pin_id)["facts"]["fish_per_hour"] is None


def test_edited_times_feed_fish_per_hour(db):
    pin_id = _pin(db, HOME)
    sid = _trip(db, _stop(HOME), catches=[{"species": "creek chub", "count": 3}])["session_id"]
    store.update_session(
        db,
        1,
        sid,
        {"started_at": "2026-06-10T08:00:00+00:00", "ended_at": "2026-06-10T09:30:00+00:00"},
    )
    assert pin_service.pin_detail(db, 1, pin_id)["facts"]["fish_per_hour"] == 2.0


# ── rename, favourite, merge, delete ─────────────────────────────────────────


def test_rename_and_favourite(db):
    pin_id = _pin(db, HOME)
    assert store.update_pin(db, 1, pin_id, {"name": "Upper riffle", "is_favourite": True})
    pin = store.get_pin(db, 1, pin_id)
    assert pin["name"] == "Upper riffle" and pin["is_favourite"] == 1
    assert pin_service.pin_detail(db, 1, pin_id)["facts"]["is_favourite"] is True


def test_merge_moves_trips_and_recalled_and_keeps_every_trip(db):
    a = _pin(db, HOME, name="A", notes="park on the left")
    b = _pin(db, FAR, name="B", is_favourite=True)
    sid_a = _trip(db, _stop(HOME))["session_id"]
    sid_b = _trip(db, _stop(FAR))["session_id"]
    store.add_recalled_visit(db, 1, a, "2019", ["creek chub"], None)

    assert store.merge_pins(db, 1, a, b)
    assert store.get_pin(db, 1, a) is None
    detail = pin_service.pin_detail(db, 1, b)
    assert {t["session_id"] for t in detail["trips"]} == {sid_a, sid_b}
    assert len(detail["recalled"]) == 1
    assert detail["pin"]["is_favourite"] is True
    assert detail["pin"]["notes"] == "park on the left"
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2


def test_merge_collapses_a_trip_linked_to_both_pins_and_keeps_a_rating(db):
    a = _pin(db, HOME, name="A")
    b = _pin(db, FAR, name="B")
    sid = _trip(db, _stop(HOME), _stop(FAR))["session_id"]
    store.set_trip_pin_rating(db, 1, sid, a, 4)
    assert store.merge_pins(db, 1, a, b)
    (trip,) = pin_service.pin_detail(db, 1, b)["trips"]
    assert trip["rating"] == 4 and len(trip["stops"]) == 2


def test_merge_into_itself_is_refused(db):
    a = _pin(db, HOME)
    assert store.merge_pins(db, 1, a, a) is False
    assert store.get_pin(db, 1, a) is not None


def test_delete_unlinks_trips_and_never_deletes_them(db):
    pin_id = _pin(db, HOME)
    result = _trip(db, _stop(HOME), catches=[{"species": "creek chub", "count": 2}])
    stop_id = result["pin_links"][0]["stop_id"]
    store.add_recalled_visit(db, 1, pin_id, "2019", [], None)

    assert store.delete_pin(db, 1, pin_id)
    assert store.get_pin(db, 1, pin_id) is None
    assert store.link_for_stop(db, 1, stop_id) is None
    assert store.get_stop(db, 1, stop_id) is not None
    assert db.execute("SELECT COUNT(*) FROM catches").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM pin_recalled_visits").fetchone()[0] == 0
    # The stop is back on the map as an unpinned stop.
    assert [s["stop_id"] for s in pin_service.map_payload(db, 1)["unpinned_stops"]] == [stop_id]


def test_map_payload_lists_unpinned_stops_with_their_candidates(db):
    _trip(db, _stop(HOME))
    pin_id = store.create_pin(db, 1, "Nearby", *NEAR)  # storage only: no back-link
    (stop,) = pin_service.map_payload(db, 1)["unpinned_stops"]
    assert [c["pin_id"] for c in stop["candidates"]] == [pin_id]


def test_migration_is_idempotent(tmp_path):
    db = get_db(tmp_path / "again.db")
    store.ensure_tables(db)
    store.ensure_tables(db)
    columns = {c.name for c in db["sessions"].columns}
    assert {"rating", "started_at", "ended_at"} <= columns
