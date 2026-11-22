"""Tests for chat session persistence."""

from pathlib import Path

from sqlite_utils import Database

from src.storage.database import ensure_schema


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def test_start_and_end_session(tmp_path: Path):
    from src.storage.conversations import end_session, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    end_session(db, session_id, summary="Talked about carp fishing", user_id=1)

    row = db["chat_sessions"].get(session_id)
    assert row["summary"] == "Talked about carp fishing"
    assert row["ended_at"] is not None


def test_save_and_load_turns(tmp_path: Path):
    """Saved turns are persisted in chat_messages and update turn_count."""
    from src.storage.conversations import save_turn, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    save_turn(db, session_id, "user", "Planning a catfish trip to Dunnville", 0, user_id=1)
    save_turn(
        db,
        session_id,
        "assistant",
        "Great choice — the Grand River is excellent for cats",
        0,
        user_id=1,
    )

    rows = list(
        db.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id",
            [session_id],
        ).fetchall()
    )
    assert len(rows) == 2
    assert rows[0][0] == "user"
    assert "Dunnville" in rows[0][1]
    assert rows[1][0] == "assistant"

    session = db["chat_sessions"].get(session_id)
    assert session["turn_count"] == 1


def test_end_session_saves_summary(tmp_path: Path):
    """end_session persists the summary and ended_at on the session row."""
    from src.storage.conversations import end_session, save_turn, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    save_turn(db, session_id, "user", "Fishing Credit River tomorrow", 0, user_id=1)
    save_turn(db, session_id, "assistant", "Looks good", 0, user_id=1)
    end_session(db, session_id, summary="• User plans Credit River trip", user_id=1)
    row = db["chat_sessions"].get(session_id)
    assert row["ended_at"] is not None
    assert "Credit River trip" in row["summary"]


def test_close_marks_session_ended(tmp_path: Path):
    """close_and_update_context marks ended_at even with no client (empty session)."""
    from src.storage.conversations import close_and_update_context, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    close_and_update_context(db, session_id, [], user_id=1)

    row = db["chat_sessions"].get(session_id)
    assert row["ended_at"] is not None


def test_missing_session_does_not_crash(tmp_path: Path):
    """close_and_update_context with an unknown session_id is a no-op."""
    from src.storage.conversations import close_and_update_context

    db = _make_db(tmp_path)
    close_and_update_context(db, "nonexistent", [], user_id=1)  # should not raise


def test_skip_empty_messages(tmp_path: Path):
    """Empty and whitespace-only messages should not be saved."""
    from src.storage.conversations import save_turn, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    save_turn(db, session_id, "user", "", 0, user_id=1)
    save_turn(db, session_id, "user", "   ", 0, user_id=1)

    count = db.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", [session_id]
    ).fetchone()[0]
    assert count == 0


def test_turn_count_increments(tmp_path: Path):
    from src.storage.conversations import save_turn, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    save_turn(db, session_id, "user", "First message", 0, user_id=1)
    save_turn(db, session_id, "assistant", "First reply", 0, user_id=1)
    save_turn(db, session_id, "user", "Second message", 1, user_id=1)

    row = db["chat_sessions"].get(session_id)
    assert row["turn_count"] == 2


def test_start_session_idempotent(tmp_path: Path):
    """start_session called twice with same id should not raise."""
    from src.storage.conversations import start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    start_session(db, session_id, user_id=1)  # should be a no-op

    count = db.execute(
        "SELECT COUNT(*) FROM chat_sessions WHERE session_id = ?", [session_id]
    ).fetchone()[0]
    assert count == 1


def test_schema_has_chat_tables(tmp_path: Path):
    db = _make_db(tmp_path)
    tables = db.table_names()
    assert "chat_sessions" in tables
    assert "chat_messages" in tables


def test_session_ids_are_server_issued_and_only_resume_for_owner(db):
    from uuid import UUID

    from src.storage.conversations import get_session, start_session

    owner_id = start_session(db, "client-chosen", user_id=1)
    assert owner_id != "client-chosen"
    assert UUID(owner_id).version == 4
    assert start_session(db, owner_id, user_id=1) == owner_id
    other_id = start_session(db, owner_id, user_id=2)
    assert other_id != owner_id
    assert get_session(db, owner_id, user_id=2) is None
    assert get_session(db, other_id, user_id=2)["user_id"] == 2


def test_another_user_cannot_write_or_close_owners_session(db, monkeypatch):
    import pytest

    from src.storage import conversations as store

    session_id = store.start_session(db, user_id=1)
    store.save_turn(db, session_id, "user", "private transcript", 0, user_id=1)
    before_session = dict(db["chat_sessions"].get(session_id))
    before_messages = list(db["chat_messages"].rows)
    with pytest.raises(ValueError, match="Chat session not found"):
        store.save_turn(db, session_id, "user", "intruder", 10, user_id=2)
    store.end_session(db, session_id, "intruder summary", user_id=2)

    def unexpected_summary(*args, **kwargs):
        pytest.fail("An unowned session must not trigger summary model work")

    monkeypatch.setattr(store, "generate_session_summary", unexpected_summary)
    store.close_and_update_context(db, session_id, [], user_id=2)
    assert dict(db["chat_sessions"].get(session_id)) == before_session
    assert list(db["chat_messages"].rows) == before_messages


def test_api_reusing_another_users_session_preserves_transcript_and_attributes_usage(
    db, monkeypatch
):
    from types import SimpleNamespace

    from src.agent import chat
    from src.llm import ledger
    from src.storage.conversations import save_turn, start_session

    owner_session = start_session(db, user_id=1)
    save_turn(db, owner_session, "user", "owner secret", 0, user_id=1)
    before_session = dict(db["chat_sessions"].get(owner_session))
    before_messages = list(db["chat_messages"].rows)
    monkeypatch.setattr(chat, "get_db", lambda: db)
    # Ledger connections are closed after every call; keep them separate from db.
    db_path = db.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(ledger, "_open_db", lambda: Database(db_path))
    responses = iter(
        [
            [SimpleNamespace(type="tool_use", name="lookup", id="t1", input={})],
            [SimpleNamespace(type="text", text="other user's reply")],
        ]
    )

    def create(**kwargs):
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=next(responses),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(chat.llm, "get_client", lambda: client)
    monkeypatch.setattr("src.llm.client.get_client", lambda: client)
    monkeypatch.setattr(chat, "_execute_tool", lambda *args, **kwargs: "tool result")
    result = chat.run_chat_api(
        [{"role": "user", "content": "other question"}],
        session_id=owner_session,
        routing_enabled=False,
        user_id=2,
    )
    other_session = result["session_id"]
    assert other_session != owner_session
    assert dict(db["chat_sessions"].get(owner_session)) == before_session
    assert (
        list(db.query("SELECT * FROM chat_messages WHERE session_id = ?", [owner_session]))
        == before_messages
    )
    assert db["chat_sessions"].get(other_session)["user_id"] == 2
    other_messages = list(
        db.query("SELECT * FROM chat_messages WHERE session_id = ?", [other_session])
    )
    assert [m["content"] for m in other_messages] == ["other question", "other user's reply"]
    assert all(m["user_id"] == 2 for m in other_messages)
    for table, expected in [("tool_usage", 1), ("llm_usage", 2)]:
        rows = list(db[table].rows)
        assert len(rows) == expected
        assert all(r["user_id"] == 2 and r["session_id"] == other_session for r in rows)
