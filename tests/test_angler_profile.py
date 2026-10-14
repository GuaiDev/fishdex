"""The angler profile: storage scoped by user, derivation from behaviour,
declared values that fade, literacy per concept, and presentation that never
locks anything out."""

import json

import pytest

from src.services.context import angler_profile, recompute_user_layer, user_layer
from src.services.context import profile as prof
from src.storage import angler_profile as store
from src.storage import user_patterns
from tests.test_local_richness import RICH, record


def trip(db, user_id, date, lat, lng, species=(), name="water", sized=False, confirmed=True):
    sid = db["sessions"].insert({"date": date, "user_id": user_id}).last_pk
    stop = db["stops"].insert({
        "session_id": sid, "user_id": user_id, "location_text": name, "location_name": name,
        "lat": lat, "lng": lng, "species_caught": json.dumps(list(species)),
        "was_productive": 1 if species else 0,
    }).last_pk  # fmt: skip
    for sp in species:
        db["catches"].insert({
            "stop_id": stop, "session_id": sid, "user_id": user_id, "species": sp, "count": 1,
            "biggest_size_cm": 40.0 if sized else None,
            "species_confirmed": 1 if confirmed else 0,
        })  # fmt: skip
    return sid


def spot(db, kind, lat, lng, dam_kind=None):
    db["spots"].insert({
        "spot_id": f"{kind}:{lng}_{lat}", "kind": kind, "jurisdiction": "CA-ON",
        "lat": lat, "lng": lng, "title": "t", "tier": 1, "structure_type": kind,
        "structure_attrs_json": json.dumps({"dam_kind": dam_kind} if dam_kind else {}),
        "built_at": "2026-10-01",
    })  # fmt: skip


# Places 20+ km apart, each its own water.
PLACES = [(43.0 + 0.2 * k, -80.0 - 0.2 * k) for k in range(8)]


# ── storage ──────────────────────────────────────────────────────────────────


def test_a_skill_field_cannot_be_written(db):
    for key in ("skill_level", "expertise", "concept:not_a_term", "level"):
        with pytest.raises(ValueError):
            store.set_declared(db, 1, key, 0.5, 1.0)


_BAD_WEIGHTS = [float("inf"), float("nan"), -1.0, store.MAX_DECLARED_WEIGHT + 1]


@pytest.mark.parametrize("weight", _BAD_WEIGHTS)
def test_declared_weight_must_be_finite_and_capped(db, weight):
    with pytest.raises(ValueError):
        store.set_declared(db, 1, "orientation", 0.5, weight)
    assert store.get_values(db, 1) == {}
    store.set_declared(db, 1, "orientation", 0.5, store.MAX_DECLARED_WEIGHT)


def test_declared_values_are_scoped_to_their_user(db):
    store.set_declared(db, 1, "orientation", 0.9, 3.0)
    store.set_declared(db, 2, "orientation", 0.1, 3.0)
    assert store.get_values(db, 1)["orientation"]["declared"].value == 0.9
    assert store.get_values(db, 2)["orientation"]["declared"].value == 0.1
    assert store.clear_declared(db, 2, "orientation")
    assert "orientation" not in store.get_values(db, 2)
    assert store.get_values(db, 1)["orientation"]["declared"].value == 0.9


def test_inferred_rows_are_replaced_per_user_only(db):
    store.write_inferred(db, 1, {"orientation": (0.3, 2.0)})
    store.write_inferred(db, 2, {"orientation": (0.7, 1.0)})
    store.write_inferred(db, 1, {"size_focus": (0.5, 1.0)})
    assert set(store.get_values(db, 1)) == {"size_focus"}
    assert store.get_values(db, 2)["orientation"]["inferred"].value == 0.7


def test_concept_events_are_validated_and_scoped(db):
    with pytest.raises(ValueError):
        store.record_event(db, 1, "not_a_term", "definition_opened")
    with pytest.raises(ValueError):
        store.record_event(db, 1, "dam", "hacked")
    store.record_event(db, 1, "dam", "definition_opened", "spot:x")
    assert store.event_counts(db, 1)[("dam", "definition_opened")][0] == 1
    assert store.event_counts(db, 2) == {}


# ── orientation ──────────────────────────────────────────────────────────────


def test_returning_to_one_water_for_one_species_reads_as_specialist(db):
    record(db, 43.01, -80.01)
    for k in range(8):
        trip(db, 1, f"2025-0{(k % 6) + 4}-1{k}", 43.01, -80.01, ["common carp"])
    p = prof.derive_profile(db, 1)
    assert p.orientation.inferred.value < 0.3
    assert p.orientation.value < 0.5


def test_two_stops_on_one_water_in_one_session_are_not_a_return_trip(db):
    record(db, 43.01, -80.01)
    one = trip(db, 1, "2025-06-01", 43.01, -80.01, ["common carp"])
    prof_one = prof.derive_profile(db, 1).orientation.inferred.value
    db["stops"].insert({
        "session_id": one, "user_id": 1, "location_text": "water", "location_name": "water",
        "lat": 43.01, "lng": -80.01, "species_caught": json.dumps(["common carp"]),
        "was_productive": 1,
    })  # fmt: skip
    db["catches"].insert({
        "stop_id": db["stops"].last_pk, "session_id": one, "user_id": 1,
        "species": "common carp", "count": 1, "biggest_size_cm": None, "species_confirmed": 1,
    })  # fmt: skip
    assert prof.derive_profile(db, 1).orientation.inferred.value == prof_one
    trip(db, 1, "2025-06-08", 43.01, -80.01, ["common carp"])
    assert prof.derive_profile(db, 1).orientation.inferred.value < prof_one


def test_seeking_out_recorded_new_species_on_new_water_reads_as_lifelister(db):
    for (lat, lng), sp in zip(PLACES, RICH, strict=True):
        record(db, lat, lng)
        trip(db, 1, "2025-06-01", lat, lng, [sp])
    p = prof.derive_profile(db, 1)
    assert p.orientation.inferred.value > 0.7
    assert p.orientation.value > 0.5


def test_accidental_new_species_count_for_less_than_intended_ones(db):
    for lat, lng in PLACES:
        record(db, lat, lng)
    for (lat, lng), sp in zip(PLACES, RICH, strict=True):
        trip(db, 1, "2025-06-01", lat, lng, [sp])  # recorded there
    for k, (lat, lng) in enumerate(PLACES):
        trip(db, 2, "2025-06-01", lat, lng, [f"mystery fish {k}"])  # not recorded there
    intended = prof.derive_profile(db, 1)
    accidental = prof.derive_profile(db, 2)
    assert accidental.orientation.value < intended.orientation.value


def test_a_few_trips_barely_move_it(db):
    for (lat, lng), sp in zip(PLACES[:2], RICH[:2], strict=True):
        record(db, lat, lng)
        trip(db, 1, "2025-06-01", lat, lng, [sp])
    p = prof.derive_profile(db, 1)
    assert abs(p.orientation.value - 0.5) < 0.2
    assert p.orientation.confidence < 0.35


def test_confidence_grows_with_logged_sessions(db):
    record(db, 43.01, -80.01)
    seen = []
    for k in range(6):
        trip(db, 1, f"2025-06-0{k + 1}", 43.01, -80.01, ["common carp"])
        seen.append(prof.derive_profile(db, 1).orientation.confidence)
    assert seen == sorted(seen) and seen[-1] > seen[0]


def test_thin_data_says_less_than_well_recorded_data(db):
    for (lat, lng), sp in zip(PLACES, RICH, strict=True):
        record(db, lat, lng)
        trip(db, 1, "2025-06-01", lat, lng, [sp])
    thin_places = [(lat + 5, lng) for lat, lng in PLACES]
    record(db, 43.01, -80.01)  # a well-recorded cell elsewhere gives the region a value
    for (lat, lng), sp in zip(thin_places, RICH, strict=True):
        record(db, lat, lng, species=[sp], per_species=1)
        trip(db, 2, "2025-06-01", lat, lng, [sp])
    well = prof.derive_profile(db, 1).orientation
    thin = prof.derive_profile(db, 2).orientation
    assert abs(thin.value - 0.5) < abs(well.value - 0.5)


def test_the_same_species_in_another_season_is_specialist_evidence(db):
    record(db, 43.01, -80.01)
    record(db, 43.61, -80.61)
    trip(db, 1, "2025-04-10", 43.01, -80.01, ["common carp"])
    trip(db, 1, "2025-07-10", 43.61, -80.61, ["common carp"])
    reasons = prof.derive_profile(db, 1).session_evidence[-1].reasons
    assert any("different season" in r for r in reasons)


def test_unconfirmed_catches_are_never_read(db):
    record(db, 43.01, -80.01)
    trip(db, 1, "2025-06-01", 43.01, -80.01, ["rainbow darter"], sized=True, confirmed=False)
    p = prof.derive_profile(db, 1)
    assert p.size_focus.inferred.weight == 0 and p.micro_focus.inferred.weight == 0
    assert not any("new species" in r for e in p.session_evidence for r in e.reasons)


def test_raw_species_count_is_not_the_signal(db):
    """Many species at one well-stocked water, returned to again and again, is not lifelisting."""
    record(db, 43.01, -80.01)
    for k in range(8):
        trip(db, 1, f"2025-06-1{k}", 43.01, -80.01, RICH[: 3 + k % 3])
    assert prof.derive_profile(db, 1).orientation.value < 0.5


# ── side values ──────────────────────────────────────────────────────────────


def test_size_and_small_species_focus_are_separate_soft_values(db):
    record(db, 43.01, -80.01)
    for k in range(5):
        trip(db, 1, f"2025-06-0{k + 1}", 43.01, -80.01, ["rainbow darter"], sized=False)
        trip(db, 2, f"2025-06-0{k + 1}", 43.01, -80.01, ["common carp"], sized=True)
    darters, carp = prof.derive_profile(db, 1), prof.derive_profile(db, 2)
    assert darters.micro_focus.value > 0.5 and darters.size_focus.value == 0.0
    assert carp.size_focus.value > 0.5 and carp.micro_focus.value == 0.0


# ── literacy ─────────────────────────────────────────────────────────────────


def test_concepts_advance_from_behaviour_one_at_a_time(db):
    spot(db, "creek_junction", 43.0, -80.0)
    spot(db, "below_dam", 44.0, -80.0, dam_kind="dam")
    for k in range(3):
        trip(db, 1, f"2025-06-0{k + 1}", 43.001, -80.001, [])
    trip(db, 1, "2025-07-01", 44.001, -80.001, [])
    c = prof.derive_profile(db, 1).concepts
    assert c["confluence"].state == "familiar"
    assert c["dam"].state == "introduced" and c["tailwater"].state == "introduced"
    assert c["drop_off"].state == "unseen"
    logged = next(e for e in c["confluence"].evidence if e.kind == "logged_near")
    assert logged.sessions == 3 and logged.refs


def test_opening_a_definition_near_known_water_makes_it_familiar(db):
    spot(db, "river_mouth", 43.0, -80.0)
    trip(db, 1, "2025-06-01", 43.001, -80.001, [])
    assert prof.derive_profile(db, 1).concepts["river_mouth"].state == "introduced"
    store.record_event(db, 1, "river_mouth", "definition_opened")
    assert prof.derive_profile(db, 1).concepts["river_mouth"].state == "familiar"


def test_a_weir_teaches_weir_not_dam(db):
    spot(db, "below_dam", 43.0, -80.0, dam_kind="weir")
    trip(db, 1, "2025-06-01", 43.001, -80.001, [])
    c = prof.derive_profile(db, 1).concepts
    assert c["weir"].state == "introduced" and c["dam"].state == "unseen"


def test_another_users_behaviour_never_moves_my_concepts(db):
    spot(db, "creek_junction", 43.0, -80.0)
    for k in range(3):
        trip(db, 2, f"2025-06-0{k + 1}", 43.001, -80.001, [])
    store.record_event(db, 2, "confluence", "definition_opened")
    db["weed_reports"].insert({
        "user_id": 2, "water_body_id": "w", "jurisdiction": "CA-ON", "geometry_geojson": "{}",
        "observed_on": "2025-06-01", "bbox_min_lng": -80.0, "bbox_min_lat": 43.0,
        "bbox_max_lng": -80.0, "bbox_max_lat": 43.0,
    })  # fmt: skip
    trip(db, 1, "2025-06-01", 43.001, -80.001, [])
    c = prof.derive_profile(db, 1).concepts
    assert c["confluence"].state == "introduced"
    assert c["weed_edge"].state == "unseen"


def test_no_global_level_exists_on_the_profile(db):
    dumped = json.dumps(prof.derive_profile(db, 1).model_dump()).lower()
    for word in ("expertise", "skill", "novice", "beginner", "advanced", "expert"):
        assert word not in dumped


# ── declared values ──────────────────────────────────────────────────────────


def test_a_declared_starting_value_leads_then_fades_as_trips_are_logged(db):
    record(db, 43.01, -80.01)
    store.set_declared(db, 1, "orientation", 1.0, 6.0)
    early = prof.blend(db, prof.derive_profile(db, 1)).orientation
    assert early.value > 0.6 and early.declared is not None
    db.execute("UPDATE angler_profile_values SET updated_at = '2000-01-01T00:00:00'")
    for k in range(25):
        trip(db, 1, f"2025-06-{k + 1:02d}", 43.01, -80.01, ["common carp"])
    late = prof.blend(db, prof.derive_profile(db, 1)).orientation
    assert late.value < 0.5


def test_a_settings_override_holds_exactly_and_clears_back_to_behaviour(db):
    record(db, 43.01, -80.01)
    for k in range(6):
        trip(db, 1, f"2025-06-0{k + 1}", 43.01, -80.01, ["common carp"])
    store.set_declared(db, 1, "orientation", 0.9, 1.0, is_override=True)
    store.set_declared(db, 1, "concept:dam", 1.0, 1.0, is_override=True)
    p = prof.blend(db, prof.derive_profile(db, 1))
    assert p.orientation.value == 0.9 and p.orientation.confidence == 1.0
    assert p.concepts["dam"].state == "familiar" and p.concepts["dam"].source == "declared"
    store.clear_declared(db, 1, "orientation")
    assert prof.blend(db, prof.derive_profile(db, 1)).orientation.value < 0.5


def test_declared_values_never_reach_another_user(db):
    store.set_declared(db, 1, "orientation", 1.0, 1.0, is_override=True)
    assert prof.blend(db, prof.derive_profile(db, 2)).orientation.value == 0.5


# ── the recompute loop ───────────────────────────────────────────────────────


def test_recompute_stores_inferred_values_as_rows(db):
    record(db, 43.01, -80.01)
    trip(db, 1, "2025-06-01", 43.01, -80.01, ["common carp"])
    recompute_user_layer(db, user_id=1)
    rows = store.get_values(db, 1)
    assert {"orientation", "size_focus", "micro_focus", "concept:dam"} <= set(rows)
    assert all(set(v) == {"inferred"} for v in rows.values())
    assert store.get_values(db, 2) == {}


def test_confirming_a_catch_invalidates_the_stored_layer(db):
    sid = trip(db, 1, "2025-06-01", 43.01, -80.01, ["common carp"], confirmed=False)
    before = user_patterns.input_fingerprint(db, 1)
    db.execute("UPDATE catches SET species_confirmed = 1 WHERE session_id = ?", [sid])
    assert user_patterns.input_fingerprint(db, 1) != before


def test_concept_events_wait_for_the_next_recompute(db):
    spot(db, "river_mouth", 43.0, -80.0)
    trip(db, 1, "2025-06-01", 43.001, -80.001, [])
    recompute_user_layer(db, user_id=1)
    store.record_event(db, 1, "river_mouth", "definition_opened")
    assert user_layer(db, user_id=1).profile.concepts["river_mouth"].state == "introduced"
    trip(db, 1, "2025-06-02", 45.0, -80.0, [])
    recompute_user_layer(db, user_id=1)
    assert angler_profile(db, user_id=1).concepts["river_mouth"].state == "familiar"
