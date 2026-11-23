"""Tests for the catches table — one row per species caught at a stop."""

from src.storage.catches import (
    confirm_catch_species,
    get_catches_for_session,
    get_catches_for_sessions,
    get_personal_best,
    insert_catch,
    update_personal_best_if_higher,
)


def _session_and_stop(db):
    session_id = db["sessions"].insert({"date": "2026-06-01"}).last_pk
    stop_id = (
        db["stops"]
        .insert(
            {
                "session_id": session_id,
                "location_text": "Bronte Creek",
                "species_caught": "[]",
            }
        )
        .last_pk
    )
    return session_id, stop_id


def test_insert_catch_returns_id(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    catch_id = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="creek chub",
        photo_url="/photos/abc123.jpg",
    )
    assert isinstance(catch_id, int)
    assert catch_id > 0


def test_get_catches_for_session_round_trips_photo_fields(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="rainbow darter",
        photo_url="/photos/xyz789.jpg",
        photo_lat=43.4,
        photo_lng=-79.7,
        photo_taken_at="2026-06-01T14:00:00",
    )

    catches = get_catches_for_session(db, session_id)
    assert len(catches) == 1
    assert catches[0]["species"] == "rainbow darter"
    assert catches[0]["photo_url"] == "/photos/xyz789.jpg"
    assert catches[0]["photo_lat"] == 43.4


def test_multiple_species_at_one_stop_get_separate_rows(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="rock bass")

    catches = get_catches_for_session(db, session_id)
    assert {c["species"] for c in catches} == {"creek chub", "rock bass"}


def test_catch_with_no_photo_has_none_url(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="smallmouth bass")

    catches = get_catches_for_session(db, session_id)
    assert catches[0]["photo_url"] is None


def test_get_catches_for_sessions_batches_multiple_sessions(fast_db):
    db = fast_db
    s1, stop1 = _session_and_stop(db)
    s2, stop2 = _session_and_stop(db)

    insert_catch(db, stop_id=stop1, session_id=s1, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop2, session_id=s2, user_id=1, species="rock bass")

    by_session = get_catches_for_sessions(db, [s1, s2], user_id=1)
    assert by_session[s1][0]["species"] == "creek chub"
    assert by_session[s2][0]["species"] == "rock bass"


def test_get_catches_for_sessions_empty_list_returns_empty_dict(fast_db):
    db = fast_db
    assert get_catches_for_sessions(db, [], user_id=1) == {}


def test_insert_catch_round_trips_biggest_size_cm(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="smallmouth bass",
        count=2,
        biggest_size_cm=35.5,
        bait="spinnerbait",
    )
    catches = get_catches_for_session(db, session_id)
    assert catches[0]["biggest_size_cm"] == 35.5
    assert catches[0]["count"] == 2
    assert catches[0]["bait"] == "spinnerbait"


def test_insert_catch_round_trips_caught_at(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="pumpkinseed",
        caught_at="2026-07-19T14:00:00",
    )
    catches = get_catches_for_session(db, session_id)
    assert catches[0]["caught_at"] == "2026-07-19T14:00:00"


def test_get_personal_best_returns_none_when_unset(fast_db):
    db = fast_db
    assert get_personal_best(db, 1, "smallmouth bass") is None


def test_update_personal_best_if_higher_sets_and_beats(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)
    catch_id = insert_catch(
        db, stop_id=stop_id, session_id=session_id, user_id=1, species="smallmouth bass"
    )

    assert (
        update_personal_best_if_higher(
            db, user_id=1, species="smallmouth bass", size_cm=30.0, catch_id=catch_id
        )
        is True
    )
    assert get_personal_best(db, 1, "smallmouth bass") == 30.0

    # A smaller size does not update it and reports no change.
    assert (
        update_personal_best_if_higher(
            db, user_id=1, species="smallmouth bass", size_cm=25.0, catch_id=catch_id
        )
        is False
    )
    assert get_personal_best(db, 1, "smallmouth bass") == 30.0

    # A bigger size does.
    assert (
        update_personal_best_if_higher(
            db, user_id=1, species="smallmouth bass", size_cm=40.0, catch_id=catch_id
        )
        is True
    )
    assert get_personal_best(db, 1, "smallmouth bass") == 40.0


def test_personal_best_is_scoped_per_user(fast_db):
    db = fast_db
    session_id, stop_id = _session_and_stop(db)
    catch_id = insert_catch(
        db, stop_id=stop_id, session_id=session_id, user_id=1, species="smallmouth bass"
    )

    update_personal_best_if_higher(
        db, user_id=1, species="smallmouth bass", size_cm=30.0, catch_id=catch_id
    )
    assert get_personal_best(db, 2, "smallmouth bass") is None


def test_confirm_catch_species_reattributes_personal_best(fast_db):
    """A catch logged under a placeholder guess (e.g. "unidentified fish
    sp.") records its personal best under that placeholder at insert time.
    Confirming the real species must move the PB to the confirmed species —
    otherwise GET /fishdex (which looks up personal_bests by the *confirmed*
    species) never finds it and the PB badge silently never renders."""
    db = fast_db
    session_id, stop_id = _session_and_stop(db)
    catch_id = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="unidentified fish sp.",
        biggest_size_cm=20.0,
        species_confirmed=False,
    )
    update_personal_best_if_higher(
        db, user_id=1, species="unidentified fish sp.", size_cm=20.0, catch_id=catch_id
    )
    assert get_personal_best(db, 1, "rock bass") is None

    assert confirm_catch_species(db, catch_id, user_id=1, species="Rock Bass") == {
        "found": True,
        "is_new_pb": True,
    }

    assert get_personal_best(db, 1, "rock bass") == 20.0


def test_confirm_catch_species_removes_stale_placeholder_pb(fast_db):
    """Confirming a catch's species away from its pre-confirmation guess
    must not leave a phantom personal-best behind for the old, never-
    actually-confirmed species. Regression for a bug found in this
    session's E2E report: a catch provisionally guessed "warmouth" by
    photo vision, then confirmed by the user as "smallmouth bass", left a
    stray 'warmouth' PB row in personal_bests even though the user never
    confirmed catching a warmouth."""
    db = fast_db
    session_id, stop_id = _session_and_stop(db)
    catch_id = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="warmouth",
        biggest_size_cm=36.83,
        species_confirmed=False,
    )
    update_personal_best_if_higher(
        db, user_id=1, species="warmouth", size_cm=36.83, catch_id=catch_id
    )
    assert get_personal_best(db, 1, "warmouth") == 36.83

    assert confirm_catch_species(db, catch_id, user_id=1, species="smallmouth bass") == {
        "found": True,
        "is_new_pb": True,
    }

    assert get_personal_best(db, 1, "smallmouth bass") == 36.83
    assert get_personal_best(db, 1, "warmouth") is None


def test_confirm_catch_species_does_not_delete_unrelated_pb_under_old_species_name(fast_db):
    """The stale-PB cleanup on confirm is scoped to the catch being
    confirmed — a genuine, independently-earned PB under the old species
    name owned by a *different* catch must survive untouched. Without the
    catch_id scoping, a plain "delete personal_bests for this species"
    would wipe out a real angler's real PB just because some unrelated
    catch was once provisionally guessed as the same species."""
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    # A real, already-confirmed warmouth catch holds the PB.
    real_warmouth_catch = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="warmouth",
        biggest_size_cm=25.0,
        species_confirmed=True,
    )
    update_personal_best_if_higher(
        db, user_id=1, species="warmouth", size_cm=25.0, catch_id=real_warmouth_catch
    )

    # A second, unrelated catch was also provisionally guessed "warmouth"
    # (smaller, so it never overtook the stored PB) and gets corrected.
    other_catch = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="warmouth",
        biggest_size_cm=10.0,
        species_confirmed=False,
    )
    update_personal_best_if_higher(
        db, user_id=1, species="warmouth", size_cm=10.0, catch_id=other_catch
    )
    assert get_personal_best(db, 1, "warmouth") == 25.0  # unchanged by the smaller guess

    assert confirm_catch_species(db, other_catch, user_id=1, species="rock bass") == {
        "found": True,
        "is_new_pb": True,
    }

    # The real warmouth PB (owned by the other catch) must survive.
    assert get_personal_best(db, 1, "warmouth") == 25.0
    assert get_personal_best(db, 1, "rock bass") == 10.0


def test_confirm_catch_species_is_new_pb_false_when_not_a_record(fast_db):
    """Confirming a catch's species must report is_new_pb=False when the
    confirmed species already has a bigger recorded best — the summary
    card's PB flag must only ever fire on a genuine record, not on every
    confirm call."""
    db = fast_db
    session_id, stop_id = _session_and_stop(db)

    existing_record_catch = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="rock bass",
        biggest_size_cm=30.0,
        species_confirmed=True,
    )
    update_personal_best_if_higher(
        db, user_id=1, species="rock bass", size_cm=30.0, catch_id=existing_record_catch
    )

    smaller_catch = insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="unidentified fish sp.",
        biggest_size_cm=15.0,
        species_confirmed=False,
    )

    assert confirm_catch_species(db, smaller_catch, user_id=1, species="rock bass") == {
        "found": True,
        "is_new_pb": False,
    }
    assert get_personal_best(db, 1, "rock bass") == 30.0  # untouched — 15.0 never beat it


def test_confirm_catch_species_not_found_returns_false(fast_db):
    db = fast_db
    assert confirm_catch_species(db, 9999, user_id=1, species="rock bass") == {
        "found": False,
        "is_new_pb": False,
    }


def test_batch_catches_excludes_other_owner_even_with_their_session_id(fast_db):
    session_id, stop_id = _session_and_stop(fast_db)
    insert_catch(fast_db, stop_id=stop_id, session_id=session_id, user_id=1, species="chub")
    assert get_catches_for_sessions(fast_db, [session_id], user_id=2) == {session_id: []}
