"""Tests for the rolling angler context document."""

from pathlib import Path

from sqlite_utils import Database

from src.storage.database import ensure_schema


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    return db


def test_load_context_empty(tmp_path: Path):
    """Returns None when no context exists."""
    from src.storage.angler_context import load_context

    db = _make_db(tmp_path)
    result = load_context(db, user_id=1)
    assert result is None


def test_save_and_load_context(tmp_path: Path):
    """Saved context is retrievable."""
    from src.storage.angler_context import load_context, save_context

    db = _make_db(tmp_path)
    save_context(db, "## Active plans\n- Dunnville Saturday", user_id=1)
    result = load_context(db, user_id=1)
    assert result is not None
    assert "Dunnville" in result


def test_format_context_for_prompt(tmp_path: Path):
    """Formatted context includes header and content."""
    from src.storage.angler_context import format_context_for_prompt

    formatted = format_context_for_prompt("## Active plans\n- Dunnville")
    assert "What I know about your fishing" in formatted
    assert "Dunnville" in formatted
    assert "---" in formatted


def test_context_single_row(tmp_path: Path):
    """Repeated saves never create more than one row."""
    from src.storage.angler_context import save_context

    db = _make_db(tmp_path)
    save_context(db, "first", user_id=1)
    save_context(db, "second", user_id=1)
    count = db.execute("SELECT COUNT(*) FROM angler_context").fetchone()[0]
    assert count == 1


def test_save_replaces_content(tmp_path: Path):
    """Second save overwrites previous content."""
    from src.storage.angler_context import load_context, save_context

    db = _make_db(tmp_path)
    save_context(db, "first version", user_id=1)
    save_context(db, "second version", user_id=1)
    assert load_context(db, user_id=1) == "second version"


def test_empty_string_returns_none(tmp_path: Path):
    """Saving an empty string means load_context returns None."""
    from src.storage.angler_context import load_context, save_context

    db = _make_db(tmp_path)
    save_context(db, "   ", user_id=1)
    assert load_context(db, user_id=1) is None


def test_close_and_update_skips_empty_session(tmp_path: Path):
    """Sessions with no turns don't trigger context update or crash."""
    from src.storage.angler_context import load_context
    from src.storage.conversations import close_and_update_context, start_session

    db = _make_db(tmp_path)
    session_id = start_session(db, user_id=1)
    close_and_update_context(db, session_id, [], user_id=1)
    assert load_context(db, user_id=1) is None


def test_schema_has_angler_context_table(tmp_path: Path):
    """ensure_schema creates the angler_context table."""
    db = _make_db(tmp_path)
    assert "angler_context" in db.table_names()
