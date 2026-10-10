"""One user's spots must never reach another user.

Covers the synthesis cache (a cached reply is built from the asker's own
catches and visits) and dismissed_segments (a blank stop hides water from
Explore). No model calls; all databases are temporary.
"""

import json

import pytest

from src.agent.tools import execute_tool
from src.services.context import _seen_segment_ids
from src.services.synthesis_cache import get_cached_synthesis, store_synthesis
from src.services.trip_logger import _penalise_segment
from src.storage.database import (
    get_db,
    migrate_dismissed_segments_user,
    migrate_segment_synthesis_user,
)

LAT, LNG = 43.5, -79.7


@pytest.fixture()
def db(tmp_path):
    return get_db(tmp_path / "test.db")


# ── synthesis cache ──────────────────────────────────────────────────────────


def test_cached_reply_is_not_served_to_another_user_by_coordinates(db):
    store_synthesis(db, "A caught 3 bass here", user_id=1, lat=LAT, lng=LNG)
    assert get_cached_synthesis(db, user_id=2, lat=LAT, lng=LNG) is None
    # Nearby point (proximity path) too
    assert get_cached_synthesis(db, user_id=2, lat=LAT + 0.0005, lng=LNG) is None


def test_cached_reply_is_not_served_to_another_user_by_name(db):
    store_synthesis(db, "A's secret spot", user_id=1, location_name="Hidden Creek")
    assert get_cached_synthesis(db, user_id=2, location_name="Hidden Creek") is None
    # Fuzzy name path
    assert get_cached_synthesis(db, user_id=2, location_name="Hidden Creek Pond") is None


def test_owner_still_hits_own_cache(db):
    store_synthesis(db, "mine", user_id=1, lat=LAT, lng=LNG, location_name="Hidden Creek")
    assert get_cached_synthesis(db, user_id=1, lat=LAT, lng=LNG)["synthesis"] == "mine"
    assert get_cached_synthesis(db, user_id=1, location_name="Hidden Creek")["synthesis"] == "mine"


def test_two_users_keep_separate_rows_for_the_same_place(db):
    store_synthesis(db, "reply for A", user_id=1, lat=LAT, lng=LNG)
    store_synthesis(db, "reply for B", user_id=2, lat=LAT, lng=LNG)
    assert get_cached_synthesis(db, user_id=1, lat=LAT, lng=LNG)["synthesis"] == "reply for A"
    assert get_cached_synthesis(db, user_id=2, lat=LAT, lng=LNG)["synthesis"] == "reply for B"


def test_legacy_rows_without_a_user_are_never_served(db):
    # A row as written before the user_id column existed.
    db.execute(
        "INSERT INTO segment_synthesis (cache_key, lat, lng, location_name, synthesis)"
        " VALUES (?, ?, ?, ?, ?)",
        [f"geo:{LAT},{LNG}", LAT, LNG, "Old Creek", "old shared reply"],
    )
    db.conn.commit()
    for uid in (1, 2):
        assert get_cached_synthesis(db, user_id=uid, lat=LAT, lng=LNG) is None
        assert get_cached_synthesis(db, user_id=uid, location_name="Old Creek") is None


def test_synthesis_migration_adds_user_id_to_old_table(tmp_path):
    db = get_db(tmp_path / "old.db")
    db.execute("DROP TABLE segment_synthesis")
    db.execute(
        "CREATE TABLE segment_synthesis (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " cache_key TEXT UNIQUE NOT NULL, lat REAL, lng REAL, location_name TEXT,"
        " jurisdiction TEXT, synthesis TEXT NOT NULL, data_sources TEXT,"
        " computed_at TEXT, hit_count INTEGER DEFAULT 0)"
    )
    db.execute(
        "INSERT INTO segment_synthesis (cache_key, lat, lng, synthesis)"
        " VALUES ('geo:1,1', 1, 1, 'x')"
    )
    migrate_segment_synthesis_user(db)
    migrate_segment_synthesis_user(db)  # idempotent
    assert "user_id" in {c.name for c in db["segment_synthesis"].columns}
    assert get_cached_synthesis(db, user_id=1, lat=1, lng=1) is None


def test_chat_web_path_never_serves_another_users_cache(tmp_path, monkeypatch):
    """Through run_chat_api: user 2 asking about user 1's cached place gets a fresh answer."""
    import src.agent.chat as chat
    import src.agent.router as router
    import src.storage.database as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db = get_db(tmp_path / "test.db")
    store_synthesis(db, "A's catches", user_id=1, lat=LAT, lng=LNG)

    pipeline_users = []

    def fake_pipeline(messages, session_id, mode="synthesis", user_id=1):
        pipeline_users.append(user_id)
        return {"reply": f"fresh answer for user {user_id}", "tool_calls": []}

    monkeypatch.setattr(chat, "_run_full_pipeline", fake_pipeline)
    monkeypatch.setattr(chat, "_log_routing", lambda *a, **k: None)
    monkeypatch.setattr(router, "classify_message", lambda *a, **k: {"mode": "synthesis"})
    monkeypatch.setattr(
        router,
        "extract_location_from_message",
        lambda m: {"lat": LAT, "lng": LNG, "location_name": None},
    )

    msgs = [{"role": "user", "content": "tell me about this spot"}]
    result = chat.run_chat_api(list(msgs), session_id="s", user_id=2)
    assert result["reply"] == "fresh answer for user 2"
    assert "A's catches" not in json.dumps(result)
    assert pipeline_users == [2]
    # ...and B's fresh reply is now cached for B only.
    assert get_cached_synthesis(db, user_id=2, lat=LAT, lng=LNG)["synthesis"] == (
        "fresh answer for user 2"
    )
    assert get_cached_synthesis(db, user_id=1, lat=LAT, lng=LNG)["synthesis"] == "A's catches"


def test_chat_web_path_cache_hit_serves_only_the_askers_own_reply(tmp_path, monkeypatch):
    """A repeat question by user 2 is a cache hit on user 2's reply; user 1's row is untouched."""
    import types

    import src.agent.chat as chat
    import src.agent.router as router
    import src.storage.database as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db = get_db(tmp_path / "test.db")
    store_synthesis(db, "A's catches", user_id=1, lat=LAT, lng=LNG)

    pipeline_users = []

    def fake_pipeline(messages, session_id, mode="synthesis", user_id=1):
        pipeline_users.append(user_id)
        return {"reply": f"fresh answer for user {user_id}", "tool_calls": []}

    seen_prompts = []

    class FakeClient:
        class messages:
            @staticmethod
            def create(model, max_tokens, messages):
                seen_prompts.append(messages[0]["content"])
                block = types.SimpleNamespace(type="text", text="rewritten from cache")
                usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
                return types.SimpleNamespace(content=[block], usage=usage)

    monkeypatch.setattr(chat, "_run_full_pipeline", fake_pipeline)
    monkeypatch.setattr(chat, "_log_routing", lambda *a, **k: None)
    monkeypatch.setattr(chat, "get_client", lambda: FakeClient)
    monkeypatch.setattr(router, "classify_message", lambda *a, **k: {"mode": "synthesis"})
    monkeypatch.setattr(
        router,
        "extract_location_from_message",
        lambda m: {"lat": LAT, "lng": LNG, "location_name": None},
    )

    msgs = [{"role": "user", "content": "tell me about this spot"}]
    first = chat.run_chat_api(list(msgs), session_id="s", user_id=2)
    assert first["reply"] == "fresh answer for user 2"

    second = chat.run_chat_api(list(msgs), session_id="s", user_id=2)
    assert second["mode"] == "synthesis_cache_hit"
    assert pipeline_users == [2]  # the repeat did not run the pipeline again
    assert len(seen_prompts) == 1
    assert "fresh answer for user 2" in seen_prompts[0]
    assert "A's catches" not in seen_prompts[0]
    assert get_cached_synthesis(db, user_id=1, lat=LAT, lng=LNG)["synthesis"] == "A's catches"


# ── dismissed_segments ───────────────────────────────────────────────────────


def test_blank_stop_by_one_user_does_not_hide_water_from_another(db):
    _penalise_segment(db, 101, user_id=1)
    assert 101 in _seen_segment_ids(db, user_id=1)
    assert 101 not in _seen_segment_ids(db, user_id=2)


def test_dismiss_tool_is_per_user(tmp_path, monkeypatch):
    import src.storage.database as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db = get_db(tmp_path / "test.db")
    out = json.loads(execute_tool("dismiss_segment", {"ogf_id": 42}, user_id=1))
    assert out["success"] is True
    assert 42 in _seen_segment_ids(db, user_id=1)
    assert 42 not in _seen_segment_ids(db, user_id=2)


def test_both_users_can_dismiss_the_same_segment(db):
    _penalise_segment(db, 7, user_id=1)
    _penalise_segment(db, 7, user_id=2)
    assert 7 in _seen_segment_ids(db, user_id=1)
    assert 7 in _seen_segment_ids(db, user_id=2)


def _legacy_dismissed(db, rows):
    db.execute("DROP TABLE dismissed_segments")
    db.execute(
        "CREATE TABLE dismissed_segments"
        " (ogf_id INTEGER PRIMARY KEY, dismissed_at TEXT, reason TEXT)"
    )
    for ogf_id, reason in rows:
        db.execute("INSERT INTO dismissed_segments VALUES (?, '2026-01-01', ?)", [ogf_id, reason])
    db.conn.commit()


def _add_user(db, user_id):
    db.execute(
        "INSERT INTO users (id, username, display_name, role) VALUES (?, ?, ?, 'user')",
        [user_id, f"u{user_id}", f"U{user_id}"],
    )
    db.conn.commit()


def _blank_stop(db, user_id, ogf_id, was_productive=0):
    session_id = db.execute(
        "INSERT INTO sessions (date, user_id) VALUES ('2026-01-01', ?)", [user_id]
    ).lastrowid
    db.execute(
        "INSERT INTO stops (session_id, location_text, ohn_segment_id, was_productive, user_id)"
        " VALUES (?, 'creek', ?, ?, ?)",
        [session_id, str(ogf_id), was_productive, user_id],
    )
    db.conn.commit()


def _dismissed(db):
    return set(db.execute("SELECT user_id, ogf_id FROM dismissed_segments").fetchall())


def test_dismissed_segments_migration_traces_log_rows_to_every_blank_owner(db):
    _add_user(db, 2)
    _add_user(db, 3)
    _blank_stop(db, 2, 55)
    _blank_stop(db, 3, 55)
    _blank_stop(db, 1, 55, was_productive=1)
    _legacy_dismissed(db, [(55, "unproductive_trip_log")])

    counts = migrate_dismissed_segments_user(db)

    assert counts == {"traced": 2, "assigned": 0, "dropped": 0}
    assert _dismissed(db) == {(2, 55), (3, 55)}
    assert migrate_dismissed_segments_user(db) is None  # idempotent
    assert _dismissed(db) == {(2, 55), (3, 55)}


def test_dismissed_segments_migration_gives_untraced_rows_to_the_only_user(db):
    _legacy_dismissed(db, [(55, "blank"), (66, "unproductive_trip_log")])

    counts = migrate_dismissed_segments_user(db)

    assert counts == {"traced": 0, "assigned": 2, "dropped": 0}
    assert _dismissed(db) == {(1, 55), (1, 66)}
    assert 55 in _seen_segment_ids(db, user_id=1)
    assert 55 not in _seen_segment_ids(db, user_id=2)


def test_dismissed_segments_migration_drops_untraced_rows_with_several_users(db):
    _add_user(db, 2)
    _blank_stop(db, 2, 77)
    _legacy_dismissed(
        db, [(55, "blank"), (66, "unproductive_trip_log"), (77, "unproductive_trip_log")]
    )

    counts = migrate_dismissed_segments_user(db)

    assert counts == {"traced": 1, "assigned": 0, "dropped": 2}
    assert _dismissed(db) == {(2, 77)}
    assert "dismissed_segments_old" not in db.table_names()


def test_blank_stop_logged_through_log_session_hides_water_only_from_its_owner(db, monkeypatch):
    import src.services.trip_enrichment as enrichment
    import src.services.trip_enrichment_conditions as conditions
    from src.services.trip_logger import log_session

    _add_user(db, 2)
    # Keep the test offline: no model call, no weather fetch.
    monkeypatch.setattr(enrichment, "enrich_session", lambda *a, **k: {"followup_questions": []})
    monkeypatch.setattr(conditions, "enrich_session_conditions", lambda *a, **k: None)
    monkeypatch.setattr("src.agent.client.get_client", lambda: object())

    parsed = {
        "date": "2026-06-01",
        "stops": [
            {
                "location_text": "Bronte Creek",
                "ohn_segment_id": 4242,
                "species_caught": [],
                "was_productive": False,
            }
        ],
    }
    log_session(parsed, db, user_id=1)

    assert 4242 in _seen_segment_ids(db, user_id=1)
    assert 4242 not in _seen_segment_ids(db, user_id=2)
