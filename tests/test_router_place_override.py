"""The reflex path must never answer a question about specific water.

Reflex answers from general knowledge with no retrieval and no tools. That is
right for a knot and catastrophic for "does Bronte Creek hold brook trout" —
the second gets invented. The guard used to be one sentence inside the
classifier's system prompt; it is now a Python check, so these tests assert
the override fires rather than assert the prompt still contains the sentence.
"""

import json

import pytest

from src.services.context.place import _known_place_names, mentions_a_place
from src.storage.database import get_db


@pytest.fixture
def db(tmp_path):
    _known_place_names.cache_clear()
    database = fast_db
    database["stream_segments"].insert(
        {
            "ogf_id": 1,
            "name": "Bronte Creek",
            "watercourse_type": "Stream",
            "geom_wkt": "LINESTRING(-79.7 43.4, -79.701 43.401)",
            "jurisdiction": "CA-ON",
        },
        alter=True,
    )
    database["water_features"].insert(
        {
            "osm_id": "w1",
            "feature_type": "lake",
            "name": "Island Lake",
            "lat": 43.9,
            "lng": -80.1,
        },
        alter=True,
    )
    return database


# -- what counts as naming a place ---------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "How do I tie a palomar knot?",
        "What bait for channel cats?",
        "thanks, that helps",
        "best time of day for smallmouth generally",
    ],
)
def test_general_questions_name_no_place(db, message):
    assert mentions_a_place(db, message) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Does Bronte Creek hold brook trout?", "bronte creek"),
        ("is BRONTE CREEK any good in spring", "bronte creek"),
        ("anything in Island Lake?", "island lake"),
    ],
)
def test_a_named_water_is_detected(db, message, expected):
    assert mentions_a_place(db, message) == expected


def test_coordinates_count_as_a_place(db):
    assert mentions_a_place(db, "What's at 43.4675, -79.6877?") is not None


def test_a_users_own_spot_name_counts_even_if_no_gazetteer_has_it(db):
    """ "the back dam" is a place to the person who fished it six times."""
    db["sessions"].insert({"date": "2025-06-14", "user_id": 1}, alter=True)
    db["stops"].insert(
        {
            "session_id": 1,
            "user_id": 1,
            "location_name": "the back dam",
            "location_text": "the back dam",
            "species_caught": json.dumps([]),
        },
        alter=True,
    )
    assert mentions_a_place(db, "was the back dam any good last year?") == "the back dam"


def test_another_users_spot_name_does_not_count(db):
    db["sessions"].insert({"date": "2025-06-14", "user_id": 2}, alter=True)
    db["stops"].insert(
        {
            "session_id": 1,
            "user_id": 2,
            "location_name": "secret pond",
            "location_text": "secret pond",
            "species_caught": json.dumps([]),
        },
        alter=True,
    )
    assert mentions_a_place(db, "how is secret pond", user_id=1) is None


def test_longest_match_wins(db):
    """Reporting the substring would send the retrieval to the wrong creek."""
    db["stream_segments"].insert(
        {
            "ogf_id": 2,
            "name": "East Bronte Creek",
            "watercourse_type": "Stream",
            "geom_wkt": "LINESTRING(-79.8 43.5, -79.801 43.501)",
            "jurisdiction": "CA-ON",
        },
        alter=True,
    )
    _known_place_names.cache_clear()
    assert mentions_a_place(db, "how about East Bronte Creek") == "east bronte creek"


# -- the override itself --------------------------------------------------------


def _route(monkeypatch, db, message, classified_mode):
    """Run run_chat_api far enough to see which mode it settled on."""
    import src.agent.chat as chat

    seen = {}

    monkeypatch.setattr(chat, "get_db", lambda: db)
    monkeypatch.setattr(
        "src.agent.router.classify_message",
        lambda *a, **k: {"mode": classified_mode, "leading_question": None},
    )
    monkeypatch.setattr(
        "src.agent.router.handle_reflex",
        lambda *a, **k: {"reply": "generic answer", "tokens": 0},
    )

    def _fake_pipeline(messages, session_id, mode="synthesis", user_id=1):
        seen["mode"] = mode
        return {"reply": "grounded answer", "tool_calls": [], "messages": messages}

    monkeypatch.setattr(chat, "_run_full_pipeline", _fake_pipeline)

    result = chat.run_chat_api([{"role": "user", "content": message}], session_id="t")
    return result, seen


def test_a_reflex_question_about_named_water_is_forced_to_synthesis(db, monkeypatch):
    result, seen = _route(monkeypatch, db, "Does Bronte Creek hold brook trout?", "reflex")
    assert seen.get("mode") == "synthesis", "must not answer from general knowledge"
    assert result["reply"] == "grounded answer"


def test_a_genuinely_general_question_stays_on_the_cheap_path(db, monkeypatch):
    result, seen = _route(monkeypatch, db, "How do I tie a palomar knot?", "reflex")
    assert seen == {}, "no retrieval pass for a knot question"
    assert result["mode"] == "reflex"


def test_a_failing_place_check_falls_back_to_the_classifier(db, monkeypatch):
    """A broken lookup must not break the turn — it keeps today's behaviour."""
    import src.agent.chat as chat

    monkeypatch.setattr(
        "src.services.context.place.mentions_a_place",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result, seen = _route(monkeypatch, db, "Does Bronte Creek hold trout?", "reflex")
    assert result["mode"] == "reflex"
    assert seen == {}
    assert chat is not None
