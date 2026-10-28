"""The signal layer: preferred water type, the explore dial, the one algorithm, and the
`new_to_me` filter that follows them.

All databases are throwaway copies; nothing touches the real one.
"""

import shutil
from datetime import date, timedelta

import pytest

from src.models.angler_profile import SIGNALS_VERSION, SignalReading
from src.models.angler_signals import SpotCandidate
from src.models.water_body import WaterBody
from src.services.context import angler_profile
from src.services.context.signals import (
    REGISTRY,
    Signal,
    SignalInputs,
    algorithm,
    derive_signals,
    register,
)
from src.services.spots import spots_geojson
from src.storage import angler_profile as store
from src.storage import user_patterns
from src.storage.species_ranges import upsert_species_ranges
from src.storage.spots import replace_spots
from tests.test_angler_profile import trip
from tests.test_spot_card import inat, species_row, spot

CHUB = ("Semotilus atromaculatus", "Creek Chub")
ALICE, BOB = 1, 2
LNG = -80.0
STEP = 0.05  # ~5.5 km: every place is its own water, far outside every visit radius


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("signals_template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    from src.storage.database import get_db

    path = tmp_path / "signals.db"
    shutil.copy(_template_db, path)
    db = get_db(path)
    upsert_species_ranges(db, [species_row(CHUB[1], CHUB[0])])
    return db


def build_world(db, ponds=12, creeks=12, rivers=3):
    """Places on a line, each its own spot on its own water body, each with a recorded fish."""
    places: dict[str, list[tuple[float, float]]] = {}
    spots, bodies = [], []
    n = 0
    for wb_type, count in (("pond", ponds), ("creek", creeks), ("river", rivers)):
        for _ in range(count):
            lat = 43.0 + STEP * n
            n += 1
            wb_id = f"{wb_type}:w{n}"
            spots.append(
                spot(lat=lat, lng=LNG).model_copy(
                    update={"water_body_id": wb_id, "title": f"Spot {n}"}
                )
            )
            bodies.append(
                WaterBody(
                    water_body_id=wb_id,
                    jurisdiction="CA-ON",
                    name=f"W{n}",
                    name_source="osm",
                    type=wb_type,
                    built_at="2026-10-01",
                )
            )
            places.setdefault(wb_type, []).append((lat, LNG))
            inat(db, *CHUB, near=(lat, LNG))
    replace_spots(db, "CA-ON", spots, bodies)
    return places


_day = iter(range(10_000))


def go(db, user_id, place, **kw):
    """One logged trip to `place`, a week after the last one."""
    when = (date(2024, 1, 1) + timedelta(days=7 * next(_day))).isoformat()
    return trip(db, user_id, when, place[0], place[1], **kw)


def read(db, user_id, signal_id):
    return angler_profile(db, user_id=user_id).signals.get(signal_id)


def type_of(place_by_type, feature):
    lat = feature["geometry"]["coordinates"][1]
    return next(t for t, ps in place_by_type.items() if any(abs(p[0] - lat) < 1e-6 for p in ps))


# ── preferred water type ─────────────────────────────────────────────────────


def test_water_type_follows_where_the_angler_goes(db):
    world = build_world(db)
    for place in world["pond"][:8]:
        go(db, ALICE, place)
    go(db, ALICE, world["creek"][0])
    for place in world["creek"][:8]:
        go(db, BOB, place)
    alice, bob = read(db, ALICE, "water_type"), read(db, BOB, "water_type")
    assert alice.kind == "distribution"
    assert alice.shares["pond"] > 0.8 and set(alice.shares) == {"pond", "creek"}
    assert sum(alice.shares.values()) == pytest.approx(1.0, abs=0.01)
    assert alice.summary == "you mostly fish ponds"
    assert bob.shares == {"creek": 1.0} and bob.summary == "you mostly fish creeks"


def test_a_shift_in_what_the_angler_fishes_moves_the_profile(db):
    world = build_world(db)
    for place in world["pond"][:10]:
        go(db, ALICE, place)
    before = read(db, ALICE, "water_type").shares
    for place in world["creek"][:10]:
        go(db, ALICE, place)
    after = read(db, ALICE, "water_type").shares
    assert before["pond"] == 1.0
    # Ten and ten, but the recent ten count for more.
    assert after["creek"] > 0.55 > after["pond"]


def test_a_stop_with_no_known_water_is_counted_not_guessed(db):
    world = build_world(db)
    for place in world["pond"][:4]:
        go(db, ALICE, place)
    go(db, ALICE, (10.0, 10.0))
    reading = read(db, ALICE, "water_type")
    assert reading.unresolved == 1
    assert reading.shares == {"pond": 1.0}


def test_catch_outcomes_and_sizes_do_not_move_water_type(db):
    world = build_world(db)
    for place in world["pond"][:3]:
        go(db, ALICE, place, species=("creek chub",), sized=True)
        go(db, BOB, place)
    for place in world["creek"][:3]:
        go(db, ALICE, place)
        go(db, BOB, place, species=("creek chub",), sized=True)
    a, b = read(db, ALICE, "water_type"), read(db, BOB, "water_type")
    assert a.shares == b.shares


def test_a_saved_pin_counts_for_less_than_a_trip(db):
    world = build_world(db)
    go(db, ALICE, world["pond"][0])
    lat, lng = world["creek"][0]
    db.execute(
        "INSERT INTO pins (user_id, name, lat, lng, jurisdiction) VALUES (?, 'p', ?, ?, 'CA-ON')",
        [ALICE, lat, lng],
    )
    db.conn.commit()
    shares = read(db, ALICE, "water_type").shares
    assert shares["pond"] > shares["creek"] > 0


# ── the explore dial ─────────────────────────────────────────────────────────


def test_explorers_and_regulars_read_differently(db):
    world = build_world(db)
    for place in world["pond"][:10]:
        go(db, ALICE, place)
    for i in range(10):
        go(db, BOB, world["pond"][i % 2])
    a, b = read(db, ALICE, "explore"), read(db, BOB, "explore")
    assert a.value > 0.6 and a.summary == "you tend to try new water"
    assert b.value < 0.4 and b.summary == "you tend to go back to the same water"


def test_one_logged_trip_says_nothing_about_exploring(db):
    world = build_world(db)
    go(db, ALICE, world["pond"][0])
    assert read(db, ALICE, "explore") is None


def test_a_declared_dial_is_stored_with_source_and_weight_and_fades(db):
    world = build_world(db)
    store.set_declared(db, ALICE, "signal:explore", 0.9, 3.0)
    declared = read(db, ALICE, "explore")
    assert declared.declared.value == 0.9 and declared.declared.weight == 3.0
    assert declared.inferred is None
    assert 0.5 < declared.value < 0.9
    assert declared.summary.endswith("(you set this)")
    # Behaviour as a regular takes over as sessions are logged after the answer.
    for i in range(24):
        go(db, ALICE, world["pond"][i % 2])
    assert read(db, ALICE, "explore").value < declared.value


def test_an_override_holds_exactly_and_clears_back_to_behaviour(db):
    store.set_declared(db, ALICE, "signal:explore", 0.2, 1.0, is_override=True)
    held = read(db, ALICE, "explore")
    assert held.value == 0.2 and held.confidence == 1.0
    assert store.clear_declared(db, ALICE, "signal:explore")
    assert read(db, ALICE, "explore") is None


def test_only_a_declarable_registered_signal_can_be_declared(db):
    for key in ("signal:water_type", "signal:nope", "signal:", "signal:explore:extra"):
        with pytest.raises(ValueError):
            store.set_declared(db, ALICE, key, 0.5, 1.0)
    store.set_declared(db, ALICE, "signal:explore", 0.5, 1.0)


def test_inferred_dial_is_mirrored_as_a_row_like_the_other_values(db):
    world = build_world(db)
    for place in world["pond"][:6]:
        go(db, ALICE, place)
    angler_profile(db, user_id=ALICE)
    row = store.get_values(db, ALICE)["signal:explore"]["inferred"]
    assert row.source == "inferred" and row.weight > 0


# ── the one algorithm ────────────────────────────────────────────────────────


def reading(signal_id, kind="scalar", confidence=0.8, **kw):
    return SignalReading(signal_id=signal_id, kind=kind, confidence=confidence, **kw)


def pond_angler(explore=0.85, **kw):
    return {
        "explore": reading("explore", value=explore, **kw),
        "water_type": reading(
            "water_type", "distribution", shares={"pond": 0.7, "creek": 0.3}, **kw
        ),
    }


def cand(water_type="pond", visits=0, favourite=False):
    return SpotCandidate(spot_id="s", water_type=water_type, visits=visits, is_favourite=favourite)


def test_an_explorer_is_matched_by_unvisited_water_of_the_kind_they_fish():
    angler = pond_angler(0.85)
    fit = algorithm.fit_candidate(angler, cand("pond"))
    assert fit.keep and fit.score > 0.9
    assert "you mostly fish ponds" in fit.reasons
    assert any("try new water" in r for r in fit.reasons)
    assert not algorithm.fit_candidate(angler, cand("creek")).keep  # not the water they like
    assert not algorithm.fit_candidate(angler, cand("pond", visits=5, favourite=True)).keep
    assert not algorithm.fit_candidate(angler, cand("pond", visits=1)).keep


def test_a_regular_keeps_their_favourites_in_front():
    angler = pond_angler(0.15)
    favourite = algorithm.fit_candidate(angler, cand("pond", visits=6, favourite=True))
    assert favourite.keep
    assert "one of your regular spots" in favourite.reasons[-1] or any(
        "regular spots" in r for r in favourite.reasons
    )
    assert not algorithm.fit_candidate(angler, cand("pond")).keep
    assert favourite.score > algorithm.fit_candidate(angler, cand("creek", 6, True)).score


def test_a_mixed_angler_keeps_both_kinds_of_pond():
    angler = pond_angler(0.5)
    assert algorithm.fit_candidate(angler, cand("pond")).keep
    assert algorithm.fit_candidate(angler, cand("pond", 6, True)).keep
    assert not algorithm.fit_candidate(angler, cand("creek")).keep


def test_an_unknown_water_type_is_no_opinion_not_a_mismatch():
    fit = algorithm.fit_candidate(pond_angler(0.85), cand(None))
    assert fit.keep and fit.signals_used == ["explore"]


def test_with_nothing_active_there_is_no_fit_and_no_default():
    fit = algorithm.fit_candidate({}, cand())
    assert fit.score is None and fit.keep is False
    thin = pond_angler(0.9, confidence=0.2)
    assert algorithm.fit_candidate(thin, cand()).score is None
    summary = algorithm.personalisation(thin)
    assert summary.active is False and "Log a few trips" in summary.note


def test_behaviour_alone_needs_more_than_a_declared_answer_does():
    behaviour = reading("explore", value=0.9, confidence=0.4)
    assert not algorithm.is_active(behaviour)
    declared = behaviour.model_copy(
        update={"declared": store.ProfileValue(value=0.9, source="declared", weight=3.0)}
    )
    assert algorithm.is_active(declared)


def test_a_few_trips_do_not_narrow_the_profile(db):
    world = build_world(db)
    for place in world["pond"][:3]:
        go(db, ALICE, place)
    profile = angler_profile(db, user_id=ALICE)
    assert not algorithm.personalisation(profile.signals).active
    assert profile.signals["water_type"].summary.startswith("Early read:")


def test_a_new_signal_plugs_in_without_touching_a_consumer(db, monkeypatch):
    from src.models.angler_signals import SignalFit

    def derive(inputs):
        return SignalReading(signal_id="shade", kind="scalar", value=0.9, confidence=0.9)

    def fit(r, c):
        return SignalFit(signal_id="shade", score=0.1, reason="shady water")

    monkeypatch.setitem(
        REGISTRY,
        "shade",
        Signal(id="shade", kind="scalar", derive=derive, fit=fit, describe=lambda r: "x",
               declarable=True),
    )  # fmt: skip
    readings = derive_signals(SignalInputs(db, ALICE, []))
    assert readings["shade"].summary == "x"
    result = algorithm.fit_candidate({**pond_angler(0.85), **readings}, cand("pond"))
    assert "shade" in result.signals_used and not result.keep  # it disagrees, so it counts
    store.set_declared(db, ALICE, "signal:shade", 0.4, 1.0)


def test_registering_twice_or_declaring_a_distribution_is_refused():
    with pytest.raises(ValueError):
        register(REGISTRY["explore"])
    with pytest.raises(ValueError):
        register(
            Signal(id="x", kind="distribution", derive=None, fit=None, describe=None,
                   declarable=True)
        )  # fmt: skip


# ── the new_to_me filter follows the angler ──────────────────────────────────


def test_an_explorer_gets_unvisited_water_of_the_kind_they_fish(db):
    world = build_world(db)
    for place in world["pond"][:9]:
        go(db, ALICE, place)
    body = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    # Every one of the 27 spots has an unseen species; only unvisited ponds survive.
    assert body["meta"]["personalisation"]["active"] is True
    assert body["meta"]["count"] == 3
    assert {type_of(world, f) for f in body["features"]} == {"pond"}
    visited = {round(p[0], 4) for p in world["pond"][:9]}
    assert not {round(f["geometry"]["coordinates"][1], 4) for f in body["features"]} & visited
    props = body["features"][0]["properties"]
    assert "you mostly fish ponds" in props["fit"]["reasons"]
    assert props["new_to_you"]["count"] == 1  # the existing property is untouched


def test_a_regular_keeps_their_favourite_spots(db):
    world = build_world(db)
    for i in range(12):
        go(db, BOB, world["pond"][i % 2])
    body = spots_geojson(db, max_tier=3, new_to_me=True, user_id=BOB)
    kept = {round(f["geometry"]["coordinates"][1], 4) for f in body["features"]}
    assert kept == {round(p[0], 4) for p in world["pond"][:2]}
    assert all(
        "regular spots" in " ".join(f["properties"]["fit"]["reasons"]) for f in body["features"]
    )


def test_two_anglers_get_different_answers_from_the_same_map(db):
    world = build_world(db)
    for place in world["pond"][:9]:
        go(db, ALICE, place)
    for place in world["creek"][:9]:
        go(db, BOB, place)
    alice = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    bob = spots_geojson(db, max_tier=3, new_to_me=True, user_id=BOB)
    assert {type_of(world, f) for f in alice["features"]} == {"pond"}
    assert {type_of(world, f) for f in bob["features"]} == {"creek"}


def test_something_unseen_is_necessary_but_never_enough(db):
    world = build_world(db)
    for place in world["pond"][:9]:
        go(db, ALICE, place)
    unseen = spots_geojson(db, max_tier=3, user_id=ALICE)
    kept = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    assert unseen["meta"]["count"] == 27 and kept["meta"]["count"] == 3
    # Once the angler has caught what is recorded, the matching spots go too.
    from tests.test_new_to_you import catch

    catch(db, ALICE, "creek chub")
    assert spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)["features"] == []


def test_without_a_pattern_the_filter_shows_nothing_and_says_why(db):
    world = build_world(db)
    for place in world["pond"][:2]:
        go(db, ALICE, place)
    body = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    assert body["features"] == [] and body["meta"]["new_to_me"] is True
    assert body["meta"]["personalisation"]["active"] is False
    assert "Log a few trips" in body["meta"]["personalisation"]["note"]
    # A declared answer is enough to start: it follows the dial, with low confidence shown.
    store.set_declared(db, ALICE, "signal:explore", 0.9, 3.0)
    started = spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)
    assert started["meta"]["count"] > 0
    assert 0 < started["meta"]["personalisation"]["confidence"] < 1


def test_results_are_ordered_by_fit_not_by_tier(db):
    world = build_world(db)
    for place in world["pond"][:9]:
        go(db, ALICE, place)
    scores = [
        f["properties"]["fit"]["score"]
        for f in spots_geojson(db, max_tier=3, new_to_me=True, user_id=ALICE)["features"]
    ]
    assert scores == sorted(scores, reverse=True)


# ── cache and API ────────────────────────────────────────────────────────────


def test_a_layer_stored_under_another_signals_version_is_a_miss(db):
    fingerprint = user_patterns.input_fingerprint(db, ALICE)
    assert fingerprint.endswith(f"|signals:{SIGNALS_VERSION}")
    older = fingerprint.replace(f"signals:{SIGNALS_VERSION}", "signals:0")
    angler_profile(db, user_id=ALICE)
    assert user_patterns.load(db, ALICE, fingerprint) is not None
    assert user_patterns.load(db, ALICE, older) is None


def test_a_spots_rebuild_can_drop_every_stored_layer(db):
    angler_profile(db, user_id=ALICE)
    angler_profile(db, user_id=BOB)
    user_patterns.invalidate_all(db)
    assert user_patterns.load(db, ALICE, user_patterns.input_fingerprint(db, ALICE)) is None
    assert user_patterns.load(db, BOB, user_patterns.input_fingerprint(db, BOB)) is None


@pytest.fixture
def client(tmp_path, monkeypatch, _template_db):
    from fastapi.testclient import TestClient

    import src.storage.database as database
    from src.api.main import app, get_current_user

    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    api_db = database.get_db()
    who = {"id": ALICE}
    app.dependency_overrides[get_current_user] = lambda: dict(who)
    test_client = TestClient(app)
    test_client.who, test_client.db = who, api_db
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def test_the_profile_endpoint_gains_signals_without_losing_anything(client):
    client.put("/profile/declared/signal:explore", json={"value": 0.8, "weight": 3})
    body = client.get("/profile").json()
    assert {"orientation", "size_focus", "micro_focus", "concepts"} <= set(body)
    explore = body["signals"]["explore"]
    assert explore["declared"]["value"] == 0.8 and explore["summary"]
    assert client.put("/profile/declared/signal:water_type", json={"value": 0.5}).status_code == 400
    assert client.delete("/profile/declared/signal:explore").status_code == 200
    assert "explore" not in client.get("/profile").json()["signals"]
