"""Tests for trip_logger service functions."""

import json

import pytest


@pytest.fixture
def db_conn(db_factory, tmp_path):
    return db_factory(path=tmp_path / "test.db")


def _insert_stop(db, location_name, location_text, species, date="2025-05-15", lat=None, lng=None):
    """Insert a session + stop directly, bypassing log_session enrichment."""
    session_id = (
        db["sessions"]
        .insert(
            {
                "date": date,
                "date_approx": None,
                "overall_notes": None,
            }
        )
        .last_pk
    )
    db["stops"].insert(
        {
            "session_id": session_id,
            "location_text": location_text,
            "location_name": location_name,
            "lat": lat,
            "lng": lng,
            "ohn_segment_id": None,
            "location_method": "text_only",
            "location_confidence": None,
            "species_caught": json.dumps(species),
            "was_productive": 1 if species else 0,
            "technique": None,
            "gear": None,
            "water_level": None,
            "water_clarity": None,
            "water_temp_c": None,
            "weather_notes": None,
            "notes": None,
        }
    )
    return session_id


def test_get_trips_at_location_by_name(db_conn):
    from src.services.trip_logger import get_trips_at_location

    _insert_stop(
        db_conn,
        location_name="Byng Island Conservation Area",
        location_text="Byng Island Conservation Area",
        species=["channel catfish"],
        date="2025-05-15",
    )

    result = get_trips_at_location(db_conn, location_query="Byng Island")
    assert "channel catfish" in result
    assert "No logged trips" not in result


def test_get_trips_at_location_no_match(db_conn):
    from src.services.trip_logger import get_trips_at_location

    result = get_trips_at_location(db_conn, location_query="Nonexistent Lake")
    assert "No logged trips" in result or "No trips logged" in result


def test_get_trips_at_location_multiple_visits(db_conn):
    from src.services.trip_logger import get_trips_at_location

    _insert_stop(db_conn, "Byng Island", "Byng Island", ["channel catfish"], date="2025-05-10")
    _insert_stop(
        db_conn, "Byng Island", "Byng Island", ["bowfin", "channel catfish"], date="2025-06-01"
    )

    result = get_trips_at_location(db_conn, location_query="Byng Island")
    assert "2 visits" in result
    assert "bowfin" in result
    assert "2025-05-10" in result


def test_get_trips_at_location_proximity(db_conn):
    from src.services.trip_logger import get_trips_at_location

    _insert_stop(
        db_conn,
        location_name="Willoway Brook",
        location_text="Willoway Brook",
        species=["rainbow darter"],
        lat=43.417,
        lng=-79.774,
    )

    # Query by nearby coordinates, not name
    result = get_trips_at_location(db_conn, lat=43.418, lng=-79.775, radius_km=2.0)
    assert "rainbow darter" in result
    assert "No logged trips" not in result


def test_get_trips_at_location_blank_stop(db_conn):
    from src.services.trip_logger import get_trips_at_location

    _insert_stop(db_conn, "Bronte Creek", "Bronte Creek at Burloak", species=[])

    result = get_trips_at_location(db_conn, location_query="Bronte Creek")
    assert "no fish (blank)" in result
    assert "No logged trips" not in result


def test_log_session_inserts_one_catch_row_per_species(db_conn):
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [
            {
                "location_text": "Bronte Creek",
                "species_caught": ["creek chub", "rock bass"],
                "photo_url": "/photos/abc123.jpg",
                "photo_lat": 43.4,
                "photo_lng": -79.7,
                "photo_taken_at": "2026-06-01T14:00:00",
            }
        ],
    }

    result = log_session(parsed, db_conn, user_id=1)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert {c["species"] for c in catches} == {"creek chub", "rock bass"}
    assert all(c["photo_url"] == "/photos/abc123.jpg" for c in catches)
    assert all(c["photo_lat"] == 43.4 for c in catches)


def test_log_session_no_species_inserts_no_catches(db_conn):
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": []}],
    }

    result = log_session(parsed, db_conn, user_id=1)
    assert get_catches_for_session(db_conn, result["session_id"]) == []


# ── parse_size_to_cm ────────────────────────────────────────────────────────


def test_parse_size_to_cm_bare_number_assumed_cm():
    from src.services.trip_logger import parse_size_to_cm

    assert parse_size_to_cm(35) == 35.0
    assert parse_size_to_cm(35.5) == 35.5
    assert parse_size_to_cm("35") == 35.0


def test_parse_size_to_cm_converts_inches():
    from src.services.trip_logger import parse_size_to_cm

    assert parse_size_to_cm("14in") == pytest.approx(35.56)
    assert parse_size_to_cm("14 inches") == pytest.approx(35.56)
    assert parse_size_to_cm('14"') == pytest.approx(35.56)


def test_parse_size_to_cm_explicit_cm_suffix():
    from src.services.trip_logger import parse_size_to_cm

    assert parse_size_to_cm("35cm") == 35.0
    assert parse_size_to_cm("35.5 cm") == 35.5


def test_parse_size_to_cm_invalid_or_empty_returns_none():
    from src.services.trip_logger import parse_size_to_cm

    assert parse_size_to_cm(None) is None
    assert parse_size_to_cm("") is None
    assert parse_size_to_cm("not a number") is None


# ── structured_catches (multi-catch logging UI) ──────────────────────────────


def test_structured_catch_merges_onto_matching_nl_species(db_conn):
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["smallmouth bass"]}],
    }
    structured = [
        {
            "species": "smallmouth bass",
            "count": 2,
            "biggest_size_cm": 35.0,
            "bait": "spinnerbait",
            "photo_path": None,
            "photo_url": None,
        }
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 1
    c = catches[0]
    assert c["species"] == "smallmouth bass"
    assert c["count"] == 2
    assert c["biggest_size_cm"] == 35.0
    assert c["bait"] == "spinnerbait"
    # Merged rows still come from the NL/vision suggestion flow, so they're
    # unconfirmed like any NL-parsed catch.
    assert c["species_confirmed"] == 0
    assert len(result["pending_catches"]) == 1


def test_structured_catch_with_no_nl_match_becomes_own_row(db_conn):
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["creek chub"]}],
    }
    structured = [
        {
            "species": "rock bass",
            "count": 1,
            "biggest_size_cm": 20.0,
            "bait": "worm",
            "photo_path": None,
            "photo_url": None,
        }
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = {c["species"]: c for c in get_catches_for_session(db_conn, result["session_id"])}
    assert set(catches) == {"creek chub", "rock bass"}
    # No photo to reconcile against — directly typed, trusted human input,
    # so it's already confirmed and never shows up as pending.
    assert catches["rock bass"]["species_confirmed"] == 1
    assert catches["rock bass"]["count"] == 1
    assert catches["rock bass"]["biggest_size_cm"] == 20.0
    assert catches["creek chub"]["species_confirmed"] == 0


def test_duplicate_species_structured_catches_get_separate_rows(db_conn):
    """Two catch cards for the same species (different sizes/bait) must not
    collapse into one row — the count stepper on a single card is how a user
    logs "N of the same fish"; two cards means two distinct catch events."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["smallmouth bass"]}],
    }
    structured = [
        {
            "species": "smallmouth bass",
            "count": 1,
            "biggest_size_cm": 35.0,
            "bait": "spinnerbait",
            "photo_path": None,
            "photo_url": None,
        },
        {
            "species": "smallmouth bass",
            "count": 1,
            "biggest_size_cm": 25.0,
            "bait": "worm",
            "photo_path": None,
            "photo_url": None,
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 2
    sizes = sorted(c["biggest_size_cm"] for c in catches)
    assert sizes == [25.0, 35.0]


def test_structured_catches_empty_is_pure_nl_behavior(db_conn):
    """structured_catches=[] must produce byte-identical rows to omitting it."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["creek chub"]}],
    }

    result = log_session(parsed, db_conn, user_id=1, structured_catches=[])
    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 1
    assert catches[0]["species"] == "creek chub"
    assert catches[0]["species_confirmed"] == 0
    assert catches[0]["count"] is None
    assert catches[0]["biggest_size_cm"] is None


def test_structured_catch_no_species_photo_only_keeps_count_size_bait(db_conn, tmp_path):
    """A structured catch with no typed species (the "let vision suggest it"
    card) must still create a row carrying count/biggest_size_cm/bait from
    the structured entry — not silently drop them while only the species
    placeholder and photo survive. The catch relies on the stop's shared
    photo (as /log-trip/photo attaches it) since it has no photo of its own."""
    from PIL import Image

    from src.services.trip_logger import log_session
    from src.storage.catches import confirm_catch_species, get_catches_for_session

    photo_path = tmp_path / "fish.jpg"
    Image.new("RGB", (10, 10)).save(photo_path)

    parsed = {
        "date": "2026-06-01",
        "stops": [
            {
                "location_text": "Bronte Creek",
                "species_caught": [],
                "photo_path": str(photo_path),
                "photo_url": "/photos/shared.jpg",
            }
        ],
    }
    structured = [
        {
            "species": None,
            "count": 3,
            "biggest_size_cm": 22.0,
            "bait": "worm",
            "photo_path": None,
            "photo_url": None,
        }
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 1
    c = catches[0]
    assert c["count"] == 3
    assert c["biggest_size_cm"] == 22.0
    assert c["bait"] == "worm"
    assert c["photo_url"] == "/photos/shared.jpg"
    assert c["species_confirmed"] == 0
    assert len(result["pending_catches"]) == 1
    assert result["pending_catches"][0]["catch_id"] == c["id"]

    # Confirming species must attach to this same row, not spawn a new one.
    result2 = confirm_catch_species(db_conn, c["id"], user_id=1, species="rock bass")
    assert result2["found"] is True
    confirmed = get_catches_for_session(db_conn, result["session_id"])
    assert len(confirmed) == 1
    assert confirmed[0]["species"] == "rock bass"
    assert confirmed[0]["count"] == 3
    assert confirmed[0]["biggest_size_cm"] == 22.0
    assert confirmed[0]["bait"] == "worm"


def test_structured_catch_updates_personal_best(db_conn):
    from src.services.trip_logger import log_session
    from src.storage.catches import get_personal_best

    parsed = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["smallmouth bass"]}],
    }

    # First-ever catch of the species is trivially a new PB (nothing to beat).
    result1 = log_session(
        parsed,
        db_conn,
        user_id=1,
        structured_catches=[
            {
                "species": "smallmouth bass",
                "count": 1,
                "biggest_size_cm": 30.0,
                "bait": None,
                "photo_path": None,
                "photo_url": None,
            },
        ],
    )
    assert get_personal_best(db_conn, 1, "smallmouth bass") == 30.0
    assert result1["catches"][0]["is_new_pb"] is True

    # A smaller catch afterward must not overwrite the recorded best, and
    # the summary card's PB flag must report False — a routine catch, not
    # a record.
    parsed2 = {
        "date": "2026-06-02",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["smallmouth bass"]}],
    }
    result2 = log_session(
        parsed2,
        db_conn,
        user_id=1,
        structured_catches=[
            {
                "species": "smallmouth bass",
                "count": 1,
                "biggest_size_cm": 22.0,
                "bait": None,
                "photo_path": None,
                "photo_url": None,
            },
        ],
    )
    assert get_personal_best(db_conn, 1, "smallmouth bass") == 30.0
    assert result2["catches"][0]["is_new_pb"] is False

    # A bigger catch must update it and report a genuine new PB.
    parsed3 = {
        "date": "2026-06-03",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["smallmouth bass"]}],
    }
    result3 = log_session(
        parsed3,
        db_conn,
        user_id=1,
        structured_catches=[
            {
                "species": "smallmouth bass",
                "count": 1,
                "biggest_size_cm": 38.0,
                "bait": None,
                "photo_path": None,
                "photo_url": None,
            },
        ],
    )
    assert get_personal_best(db_conn, 1, "smallmouth bass") == 38.0
    assert result3["catches"][0]["is_new_pb"] is True


def test_log_session_catches_summary_includes_confirmed_and_pending_rows(db_conn):
    """The "catches" summary (distinct from pending_catches, which only
    covers unconfirmed rows) must include every catch inserted this call —
    the end-of-session summary card's total-fish/species-count/biggest-catch
    stats need already-confirmed rows (typed species, fast-tally taps) that
    never show up in pending_catches at all."""
    from src.services.trip_logger import log_session

    parsed = {
        "date": "2026-07-20",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    structured = [
        {
            "species": "smallmouth bass",
            "count": 1,
            "biggest_size_cm": 35.0,
            "bait": "spinnerbait",
            "photo_path": None,
            "photo_url": None,
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T14:00:00",
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    assert len(result["catches"]) == 2
    by_species = {c["species"]: c for c in result["catches"]}
    assert by_species["smallmouth bass"]["species_confirmed"] is True
    assert by_species["smallmouth bass"]["biggest_size_cm"] == 35.0
    assert by_species["unidentified sp."]["species_confirmed"] is True
    assert by_species["unidentified sp."]["biggest_size_cm"] is None
    # Neither is pending — both were confirmed at insert time.
    assert result["pending_catches"] == []


def test_fast_tally_no_species_no_photo_inserts_unidentified(db_conn):
    """A bare '+1 fish' tap — no species, no photo, no NL match — must be
    recorded as a trusted-but-unidentified catch, not silently dropped.
    Unlike the vision-suggested paths, there's no AI guess here to confirm,
    so it's already species_confirmed=1 and never enters the pending queue."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-07-19",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    structured = [
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T14:03:00",
        }
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 1
    c = catches[0]
    assert c["species"] == "unidentified sp."
    assert c["species_confirmed"] == 1
    assert c["caught_at"] == "2026-07-19T14:03:00"
    assert result["pending_catches"] == []


def test_multiple_fast_tally_taps_become_separate_rows(db_conn):
    """Each '+1 fish' tap is its own catch event — taps must not merge into
    one row with an incremented count, or per-tap timestamps would be lost."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-07-19",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    structured = [
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T14:00:00",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T14:05:00",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T16:40:00",
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 3
    assert all(c["species"] == "unidentified sp." for c in catches)
    assert sorted(c["caught_at"] for c in catches) == [
        "2026-07-19T14:00:00",
        "2026-07-19T14:05:00",
        "2026-07-19T16:40:00",
    ]


def test_fast_tally_does_not_ride_unrelated_stop_photo(db_conn, tmp_path):
    """A session can mix untagged '+1 fish' taps with one detailed card that
    has its own photo attached to the stop. The taps must NOT ride that
    shared photo (which would run vision against fish they were never in)
    — only the actual "let vision suggest it" detailed entry may do that."""
    from PIL import Image

    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    photo_path = tmp_path / "fish.jpg"
    Image.new("RGB", (10, 10)).save(photo_path)

    parsed = {
        "date": "2026-07-19",
        "stops": [
            {
                "location_text": "Sixteen Mile Creek",
                "species_caught": [],
                "photo_path": str(photo_path),
                "photo_url": "/photos/shared.jpg",
            }
        ],
    }
    structured = [
        # The detailed "let vision suggest it" card — no species, relies on
        # the stop's shared photo. Must still ride it (unchanged behavior).
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "detailed",
        },
        # Two anonymous fast-tally taps in the same session.
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T14:00:00",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-19T14:05:00",
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 3

    tally_rows = [
        c for c in catches if c["species"] == "unidentified sp." and c["species_confirmed"] == 1
    ]
    detailed_rows = [c for c in catches if c["species_confirmed"] == 0]

    assert len(tally_rows) == 2
    assert all(c["photo_url"] is None for c in tally_rows), (
        "fast-tally taps must not ride the unrelated stop photo"
    )

    assert len(detailed_rows) == 1
    assert detailed_rows[0]["photo_url"] == "/photos/shared.jpg", (
        "the detailed no-species card must still ride the stop's shared photo"
    )
    assert len(result["pending_catches"]) == 1
    assert result["pending_catches"][0]["catch_id"] == detailed_rows[0]["id"]


def test_fast_tally_session_with_no_notes_still_persists(db_conn):
    """Regression for the empty-notes data-loss bug: a session that's
    nothing but untagged '+1 fish' taps, with no session notes and no
    location text typed anywhere, composes (see LogTrip.jsx's
    composeSessionText) to text the NL parser can't extract any stop from
    (parse_session_from_text legitimately returns stops: [] — there's no
    location/narrative content to work with). Without a fallback,
    log_session's per-stop loop never runs and every tap is silently
    discarded even though the caller sees a normal "status": "logged"
    response. Reproduced live against Railway on 2026-07-20: a real session
    (user_id=5) logged 4 taps that all vanished, stops_logged=0. The device
    geolocation captured at "Start fishing" must be enough on its own to
    create a stop to hold them."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {"date": None, "date_approx": None, "overall_notes": None, "stops": []}
    structured = [
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T13:00:00.000Z",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T13:01:00.000Z",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T13:02:00.000Z",
        },
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T13:03:00.000Z",
        },
    ]

    result = log_session(
        parsed,
        db_conn,
        user_id=1,
        structured_catches=structured,
        fallback_lat=43.4675,
        fallback_lng=-79.6877,
    )

    assert result["stops_logged"] == 1
    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 4
    assert all(c["species"] == "unidentified sp." for c in catches)
    assert all(c["species_confirmed"] == 1 for c in catches)
    assert sorted(c["caught_at"] for c in catches) == [
        "2026-07-20T13:00:00.000Z",
        "2026-07-20T13:01:00.000Z",
        "2026-07-20T13:02:00.000Z",
        "2026-07-20T13:03:00.000Z",
    ]

    stop = next(iter(db_conn["stops"].rows_where("session_id = ?", [result["session_id"]])))
    assert stop["lat"] == 43.4675
    assert stop["lng"] == -79.6877
    assert stop["location_method"] == "device_geolocation"


def test_fast_tally_session_with_no_notes_and_no_gps_still_creates_stop(db_conn):
    """Even with no device GPS available at all (e.g. permission denied),
    structured catches must still be saved rather than dropped — the
    fallback stop is created with lat/lng null and an honest
    location_method rather than fabricating a location."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {"date": None, "date_approx": None, "overall_notes": None, "stops": []}
    structured = [
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
            "source": "fast_tally",
            "caught_at": "2026-07-20T13:00:00.000Z",
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    assert result["stops_logged"] == 1
    catches = get_catches_for_session(db_conn, result["session_id"])
    assert len(catches) == 1

    stop = next(iter(db_conn["stops"].rows_where("session_id = ?", [result["session_id"]])))
    assert stop["lat"] is None
    assert stop["location_method"] == "unknown"


def test_structured_catches_empty_does_not_synthesize_stop(db_conn):
    """The fallback-stop guarantee only kicks in when there's real
    structured data to save — a plain NL-only call with no stops and no
    structured_catches (e.g. genuinely empty/unparseable text) must still
    log zero stops, unchanged pre-existing behavior."""
    from src.services.trip_logger import log_session

    parsed = {"date": None, "date_approx": None, "overall_notes": None, "stops": []}
    result = log_session(parsed, db_conn, user_id=1, structured_catches=[])
    assert result["stops_logged"] == 0
    assert result["pending_catches"] == []


def test_non_fast_tally_no_species_no_photo_still_skipped(db_conn):
    """Regression: a structured catch with no species, no photo, and no
    source marker (i.e. anything other than 'fast_tally') must still be
    skipped rather than inserted — this is the pre-existing detailed-card
    behavior and must not change for callers that don't opt into fast-tally."""
    from src.services.trip_logger import log_session
    from src.storage.catches import get_catches_for_session

    parsed = {
        "date": "2026-07-19",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    structured = [
        {
            "species": None,
            "count": 1,
            "biggest_size_cm": None,
            "bait": None,
            "photo_path": None,
            "photo_url": None,
        },
    ]

    result = log_session(parsed, db_conn, user_id=1, structured_catches=structured)

    catches = get_catches_for_session(db_conn, result["session_id"])
    assert catches == []
    assert result["pending_catches"] == []
