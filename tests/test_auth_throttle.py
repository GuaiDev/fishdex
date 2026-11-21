from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

from sqlite_utils import Database
from sqlite_utils.db import Table

from src.auth.auth import get_user_from_token, hash_token


def _setup(tmp_path, db_factory):
    db = db_factory(tmp_path / "test.db")
    db["users"].insert(
        {
            "id": 1,
            "username": "alice",
            "display_name": "Alice",
            "role": "beta",
            "daily_message_limit": 50,
            "is_active": 1,
        },
        pk="id",
        replace=True,
    )
    db["user_sessions"].insert(
        {
            "id": 1,
            "user_id": 1,
            "token": hash_token("tok"),
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "last_used_at": None,
        },
        pk="id",
    )
    db.conn.commit()
    return db


def test_get_user_from_token_updates_when_stale(tmp_path, db_factory):
    db = _setup(tmp_path, db_factory)
    old = (datetime.now() - timedelta(minutes=10)).isoformat()
    db["user_sessions"].update(1, {"last_used_at": old})
    db["users"].update(1, {"last_seen_at": old})
    db.conn.commit()

    user = get_user_from_token(db, "tok")
    assert user and user["id"] == 1

    session = next(db["user_sessions"].rows_where("id = 1"))
    db_user = next(db["users"].rows_where("id = 1"))
    assert session["last_used_at"] != old
    assert db_user["last_seen_at"] != old


def test_get_user_from_token_skips_write_within_window(tmp_path, db_factory):
    db = _setup(tmp_path, db_factory)
    recent = (datetime.now() - timedelta(minutes=1)).isoformat()
    db["user_sessions"].update(1, {"last_used_at": recent})
    db["users"].update(1, {"last_seen_at": recent})
    db.conn.commit()

    user = get_user_from_token(db, "tok")
    assert user and user["id"] == 1

    session = next(db["user_sessions"].rows_where("id = 1"))
    db_user = next(db["users"].rows_where("id = 1"))
    assert session["last_used_at"] == recent
    assert db_user["last_seen_at"] == recent


def test_concurrent_stale_callers_touch_timestamps_once(tmp_path, monkeypatch, db_factory):
    db = _setup(tmp_path, db_factory)
    old = (datetime.now() - timedelta(minutes=10)).isoformat()
    db["user_sessions"].update(1, {"last_used_at": old})
    db["users"].update(1, {"last_seen_at": old})
    db.conn.commit()
    barrier = Barrier(2)
    rows_where = Table.rows_where

    def synchronized_rows(table, *args, **kwargs):
        rows = list(rows_where(table, *args, **kwargs))
        if table.name == "user_sessions":
            assert rows[0]["last_used_at"] == old
            barrier.wait(timeout=10)
        return iter(rows)

    monkeypatch.setattr(Table, "rows_where", synchronized_rows)

    def authenticate():
        caller = Database(tmp_path / "test.db")
        try:
            before = caller.conn.total_changes
            user = get_user_from_token(caller, "tok")
            assert user and user["id"] == 1
            return caller.conn.total_changes - before
        finally:
            caller.conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = list(pool.map(lambda _: authenticate(), range(2)))

    assert sorted(changes) == [0, 2]
    session = db.execute("SELECT last_used_at FROM user_sessions WHERE id = 1").fetchone()
    user = db.execute("SELECT last_seen_at FROM users WHERE id = 1").fetchone()
    assert session[0] != old
    assert session[0] == user[0]
