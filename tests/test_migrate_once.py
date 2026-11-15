"""Migrations run once; opening the database afterwards writes nothing.

`get_db()` used to run the schema check and every migration on every call, nine of
them writes, so any request failed with "database is locked" while another process
held the write lock. Every database here is temporary.
"""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from sqlite_utils import Database
from typer.testing import CliRunner

import src.storage.database as database
from src.api.main import app
from src.storage.database import MIGRATIONS, get_db, migrate, pending_migrations

WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP", "BEGIN")


@pytest.fixture
def traced(monkeypatch):
    """Every statement any connection opened through get_db() runs, in order."""
    statements: list[str] = []
    configure = database._configure_connection

    def configure_and_trace(db):
        configure(db)
        db.conn.set_trace_callback(statements.append)

    monkeypatch.setattr(database, "_configure_connection", configure_and_trace)
    return statements


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith(WRITE_VERBS)]


@pytest.mark.raw_db
def test_get_db_on_a_migrated_database_writes_nothing(tmp_path, traced):
    path = tmp_path / "t.db"
    migrate(get_db(path))
    traced.clear()

    db = get_db(path)

    assert _writes(traced) == []
    assert db.conn.total_changes == 0


@pytest.mark.raw_db
def test_get_db_does_not_build_the_schema(tmp_path):
    db = get_db(tmp_path / "t.db")

    assert db.table_names() == []


@pytest.mark.raw_db
def test_get_db_sets_the_pragmas(tmp_path):
    conn = get_db(tmp_path / "t.db").conn

    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0


@pytest.mark.raw_db
def test_migrate_records_every_step_and_the_second_run_does_nothing(tmp_path, traced):
    db = get_db(tmp_path / "t.db")

    ran = migrate(db)

    assert [m.version for m in ran] == [m.version for m in MIGRATIONS]
    rows = db.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    assert rows == [(m.version, m.name) for m in MIGRATIONS]
    assert pending_migrations(db) == []

    traced.clear()
    assert migrate(db) == []
    assert _writes(traced) == []


@pytest.mark.raw_db
@pytest.mark.parametrize("setting", [0, 1])
def test_migrate_restores_the_foreign_keys_setting(tmp_path, setting):
    db = get_db(tmp_path / "t.db")
    db.execute(f"PRAGMA foreign_keys = {setting}")
    migrate(db)

    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == setting


@pytest.mark.raw_db
def test_a_database_from_before_numbering_migrates_once_and_is_backfilled(tmp_path):
    """Production today: every table exists, nothing records that migrations ran."""
    path = tmp_path / "t.db"
    db = get_db(path)
    migrate(db)
    db.execute("DROP TABLE schema_migrations")
    db.execute("INSERT INTO sessions (user_id, date) VALUES (NULL, '2026-05-01')")
    db.conn.commit()

    ran = migrate(db)

    assert len(ran) == len(MIGRATIONS)
    assert db.execute("SELECT user_id FROM sessions").fetchone()[0] == 1


@pytest.mark.raw_db
def test_the_user_one_backfill_does_not_run_again(tmp_path):
    """A row written without a user stays visibly ownerless instead of becoming the captain's."""
    path = tmp_path / "t.db"
    migrate(get_db(path))
    db = get_db(path)
    db.execute("INSERT INTO sessions (user_id, date) VALUES (NULL, '2026-05-01')")
    db.conn.commit()

    migrate(get_db(path))
    get_db(path)

    assert db.execute("SELECT user_id FROM sessions").fetchone()[0] is None


@pytest.mark.raw_db
def test_a_route_serves_while_another_connection_holds_a_long_write(tmp_path, monkeypatch, traced):
    path = tmp_path / "test_fishing.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    migrate(get_db(path))
    client = TestClient(app)
    client.get("/map/spots")  # warm the lazy imports so the timing below measures the lock only
    traced.clear()

    # Another process mid-ingest: a write transaction open for the whole request,
    # which stands in for the 15 s the old per-request migrations could not wait out.
    holder = sqlite3.connect(path, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO sessions (user_id, date) VALUES (1, '2026-05-01')")
    try:
        started = time.monotonic()
        response = client.get("/map/spots")
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()

    assert response.status_code == 200
    assert elapsed < 2.0  # a lock wait would cost the full 5 s busy timeout
    assert traced, "the route never reached the database, so the trace proves nothing"
    assert _writes(traced) == []


def test_the_migrate_command_brings_a_new_database_current(tmp_path, monkeypatch):
    from src.cli.main import app as cli

    path = tmp_path / "fishing.db"
    monkeypatch.setattr(database, "DB_PATH", path)

    result = CliRunner().invoke(cli, ["migrate"])

    assert result.exit_code == 0, result.output
    assert pending_migrations(Database(path)) == []
    assert "schema current" in result.output
