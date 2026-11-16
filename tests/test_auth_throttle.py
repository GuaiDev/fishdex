from datetime import datetime, timedelta

from src.auth.auth import get_user_from_token
from src.storage.database import get_db


def _setup(tmp_path):
    db = get_db(tmp_path / "test.db")
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
    )
    db["user_sessions"].insert(
        {
            "id": 1,
            "user_id": 1,
            "token": "tok",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "last_used_at": None,
        },
        pk="id",
    )
    db.conn.commit()
    return db


def test_get_user_from_token_updates_when_stale(tmp_path):
    db = _setup(tmp_path)
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


def test_get_user_from_token_skips_write_within_window(tmp_path):
    db = _setup(tmp_path)
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
