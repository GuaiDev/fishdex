"""Session tokens are stored hashed, can be revoked, and expire strictly (plan P1.7)."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from src.api.main import app
from src.auth.auth import (
    create_session,
    get_user_from_token,
    hash_token,
    is_hashed_token,
    revoke_user_tokens,
)
from src.cli.main import app as cli
from src.storage.database import get_db, migrate

client = TestClient(app)


def _future(days=1):
    return (datetime.now() + timedelta(days=days)).isoformat()


@pytest.fixture
def db(tmp_path, db_factory, monkeypatch):
    import src.storage.database as database

    path = tmp_path / "test_fishing.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    d = db_factory(path)
    for uid, name in ((2, "alice"), (3, "bob")):
        d["users"].insert({"id": uid, "username": name, "role": "beta", "is_active": 1})
    d.conn.commit()
    return d


def _rows(db):
    return db.execute("SELECT user_id, token FROM user_sessions ORDER BY id").fetchall()


def test_new_session_stores_only_the_hash(db):
    token = create_session(db, 2)
    stored = [r[1] for r in _rows(db)]
    assert stored == [hash_token(token)]
    assert token not in stored
    assert get_user_from_token(db, token)["id"] == 2


def test_a_stored_hash_is_not_a_token(db):
    token = create_session(db, 2)
    assert get_user_from_token(db, hash_token(token)) is None


def test_migration_hashes_existing_rows_and_old_tokens_still_work(db):
    db["user_sessions"].insert(
        {"user_id": 2, "token": "legacy-plain-token", "expires_at": _future()}
    )
    db.execute("DELETE FROM schema_migrations WHERE version = 31")
    db.conn.commit()

    assert [m.version for m in migrate(db)] == [31]

    assert _rows(db) == [(2, hash_token("legacy-plain-token"))]
    assert get_user_from_token(db, "legacy-plain-token")["id"] == 2
    assert migrate(db) == []  # idempotent; hashed rows are not hashed twice
    assert get_user_from_token(db, "legacy-plain-token")["id"] == 2


def test_plaintext_row_written_after_migration_works_and_is_upgraded(db):
    db["user_sessions"].insert({"user_id": 2, "token": "late-plain", "expires_at": _future()})
    assert get_user_from_token(db, "late-plain")["id"] == 2
    assert [r[1] for r in _rows(db)] == [hash_token("late-plain")]


def test_logout_makes_the_token_401(db):
    token = create_session(db, 2)
    other = create_session(db, 3)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/logout", headers=headers).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code == 200


def test_logout_without_a_token_is_401(db):
    assert client.post("/auth/logout").status_code == 401


@pytest.mark.parametrize("expires", ["not-a-date", "", None])
def test_unparseable_expiry_is_401(db, expires):
    db["user_sessions"].insert({"user_id": 2, "token": hash_token("tok"), "expires_at": expires})
    assert get_user_from_token(db, "tok") is None
    assert client.get("/auth/me", headers={"Authorization": "Bearer tok"}).status_code == 401


def test_expired_token_is_refused(db):
    db["user_sessions"].insert(
        {"user_id": 2, "token": hash_token("old"), "expires_at": _future(-1)}
    )
    assert get_user_from_token(db, "old") is None


def test_revoke_user_tokens_only_touches_that_user(db):
    create_session(db, 2)
    create_session(db, 2)
    keep = create_session(db, 3)
    assert revoke_user_tokens(db, 2) == 2
    assert get_user_from_token(db, keep)["id"] == 3
    assert len(_rows(db)) == 1


def test_revoke_tokens_cli(db):
    token = create_session(db, 2)
    keep = create_session(db, 3)
    result = CliRunner().invoke(cli, ["revoke-tokens", "--user", "Alice"])
    assert result.exit_code == 0, result.output
    assert "Revoked 1" in result.output
    d = get_db()
    assert get_user_from_token(d, token) is None
    assert get_user_from_token(d, keep)["id"] == 3
    assert CliRunner().invoke(cli, ["revoke-tokens", "--user", "nobody"]).exit_code == 1


def test_hash_shape():
    assert is_hashed_token(hash_token("x"))
    assert not is_hashed_token("x")
