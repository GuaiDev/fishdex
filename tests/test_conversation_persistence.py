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
    start_session(db, "test-session-1")
    end_session(db, "test-session-1", summary="Talked about carp fishing")

    row = db["chat_sessions"].get("test-session-1")
    assert row["summary"] == "Talked about carp fishing"
    assert row["ended_at"] is not None


def test_save_and_load_turns(tmp_path: Path):
    """Saved turns are persisted in chat_messages and update turn_count."""
    from src.storage.conversations import save_turn, start_session

    db = _make_db(tmp_path)
    start_session(db, "test-session-2")
    save_turn(db, "test-session-2", "user", "Planning a catfish trip to Dunnville", 0)
    save_turn(
        db, "test-session-2", "assistant", "Great choice — the Grand River is excellent for cats", 0
    )

    rows = list(
        db.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id",
            ["test-session-2"],
        ).fetchall()
    )
    assert len(rows) == 2
    assert rows[0][0] == "user"
    assert "Dunnville" in rows[0][1]
    assert rows[1][0] == "assistant"

    session = db["chat_sessions"].get("test-session-2")
    assert session["turn_count"] == 1


def test_end_session_saves_summary(tmp_path: Path):
    """end_session persists the summary and ended_at on the session row."""
    from src.storage.conversations import end_session, save_turn, start_session

    db = _make_db(tmp_path)
    start_session(db, "summ-session")
    save_turn(db, "summ-session", "user", "Fishing Credit River tomorrow", 0)
    save_turn(db, "summ-session", "assistant", "Looks good", 0)
    end_session(db, "summ-session", summary="• User plans Credit River trip")

    row = db["chat_sessions"].get("summ-session")
    assert row["ended_at"] is not None
    assert "Credit River trip" in row["summary"]


def test_close_marks_session_ended(tmp_path: Path):
    """close_and_update_context marks ended_at even with no client (empty session)."""
    from src.storage.conversations import close_and_update_context, start_session

    db = _make_db(tmp_path)
    start_session(db, "close-session")
    close_and_update_context(db, "close-session", [], user_id=1)

    row = db["chat_sessions"].get("close-session")
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
    start_session(db, "test-session-3")
    save_turn(db, "test-session-3", "user", "", 0)
    save_turn(db, "test-session-3", "user", "   ", 0)

    count = db.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id='test-session-3'"
    ).fetchone()[0]
    assert count == 0


def test_turn_count_increments(tmp_path: Path):
    from src.storage.conversations import save_turn, start_session

    db = _make_db(tmp_path)
    start_session(db, "count-session")
    save_turn(db, "count-session", "user", "First message", 0)
    save_turn(db, "count-session", "assistant", "First reply", 0)
    save_turn(db, "count-session", "user", "Second message", 1)

    row = db["chat_sessions"].get("count-session")
    assert row["turn_count"] == 2


def test_start_session_idempotent(tmp_path: Path):
    """start_session called twice with same id should not raise."""
    from src.storage.conversations import start_session

    db = _make_db(tmp_path)
    start_session(db, "idempotent-session")
    start_session(db, "idempotent-session")  # should be a no-op

    count = db.execute(
        "SELECT COUNT(*) FROM chat_sessions WHERE session_id='idempotent-session'"
    ).fetchone()[0]
    assert count == 1


def test_schema_has_chat_tables(tmp_path: Path):
    db = _make_db(tmp_path)
    tables = db.table_names()
    assert "chat_sessions" in tables
    assert "chat_messages" in tables
