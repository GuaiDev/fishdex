"""The normal route-test hook must catch mutations as well as unscoped reads."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.tenancy_trace import EXCEPTIONS


@pytest.fixture
def query_client(tmp_path):
    path = tmp_path / "trace.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER, note TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER,
                            daily_message_limit INTEGER, display_name TEXT, role TEXT);
        CREATE TABLE invite_codes (id INTEGER, code TEXT, is_active INTEGER,
                                   used_by INTEGER, used_at TEXT);
        CREATE TABLE user_sessions (id INTEGER, user_id INTEGER, token TEXT,
                                    last_used_at TEXT);
        CREATE TABLE schema_migrations (version INTEGER);
        INSERT INTO sessions VALUES (1, 7, 'private');
        INSERT INTO sessions VALUES (2, 8, 'other');
    """)
    conn.close()
    app = FastAPI()

    @app.post("/query")
    def query(body: dict):
        with sqlite3.connect(path) as db:
            if body.get("observer"):
                db.set_trace_callback(lambda sql: None)
            if body.get("script"):
                db.executescript(body["sql"])
            elif body.get("many"):
                db.cursor().executemany(body["sql"], body["parameters"])
            else:
                db.execute(body["sql"], body.get("parameters", []))
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize(
    "sql, parameters",
    [
        ("SELECT * FROM sessions", []),
        ("SELECT user_id FROM sessions WHERE id = ?", [1]),
        ("SELECT * FROM sessions WHERE user_id = 7", []),
        ("SELECT * FROM sessions WHERE note = 'user_id = ?'", []),
        ("SELECT * FROM sessions -- user_id = ?", []),
        ("SELECT * FROM sessions WHERE user_id = ?", [None]),
        ("UPDATE sessions SET user_id = ?", [7]),
        ("DELETE FROM sessions WHERE id = ?", [1]),
        ("INSERT INTO sessions (user_id, note) VALUES (7, ?)", ["literal"]),
        ('WITH hidden AS (SELECT * FROM "sessions") SELECT * FROM hidden', []),
    ],
)
def test_broken_query_fails_through_request_hook(query_client, sql, parameters):
    with pytest.raises(AssertionError, match="Unscoped personal SQL"):
        query_client.post("/query", json={"sql": sql, "parameters": parameters})


@pytest.mark.parametrize(
    "sql, parameters",
    [
        ("SELECT * FROM sessions WHERE user_id = ?", [7]),
        ('SELECT * FROM "sessions" s WHERE s."user_id" = :owner', {"owner": 7}),
        ("UPDATE sessions SET note = ? WHERE user_id = ?", ["updated", 7]),
        ("DELETE FROM sessions WHERE user_id IN (?)", [7]),
        ("INSERT INTO sessions (user_id, note) VALUES (?, ?)", [7, "bound"]),
        ("SELECT * FROM users WHERE id = ?", [7]),
    ],
)
def test_bound_owner_passes_and_is_traced(query_client, tenancy_trace, sql, parameters):
    assert (
        query_client.post("/query", json={"sql": sql, "parameters": parameters}).status_code == 200
    )
    assert any(sql in trace["statements"] for trace in tenancy_trace)


def test_executemany_checks_each_binding(query_client):
    with pytest.raises(AssertionError, match="Unscoped personal SQL"):
        query_client.post(
            "/query",
            json={
                "sql": "INSERT INTO sessions (user_id, note) VALUES (?, ?)",
                "parameters": [[7, "bound"], [None, "missing"]],
                "many": True,
            },
        )


def test_script_has_no_binding(query_client):
    with pytest.raises(AssertionError, match="Unscoped personal SQL"):
        query_client.post(
            "/query",
            json={
                "sql": "SELECT 1; SELECT * FROM sessions;",
                "script": True,
            },
        )


@pytest.mark.parametrize("sql", EXCEPTIONS)
def test_exact_exception_passes(query_client, sql):
    parameters = ["token"] * sql.count("?")
    assert (
        query_client.post("/query", json={"sql": sql, "parameters": parameters}).status_code == 200
    )


@pytest.mark.parametrize("sql", EXCEPTIONS)
def test_exception_lookalike_is_rejected(query_client, sql):
    # The approved shape may not be extended to read another owner's records.
    if sql.startswith("select"):
        # A subquery preserves the exception's projected column count.
        broken = f"SELECT * FROM ({sql}) WHERE EXISTS (SELECT 1 FROM sessions)"
    elif sql.startswith("insert"):
        broken = sql + " RETURNING id"
    else:
        broken = sql + " OR EXISTS (SELECT 1 FROM sessions)"
    with pytest.raises(AssertionError, match="Unscoped personal SQL"):
        query_client.post(
            "/query",
            json={
                "sql": broken,
                "parameters": ["token"] * broken.count("?"),
            },
        )


def test_another_trace_observer_cannot_disable_enforcement(query_client):
    with pytest.raises(AssertionError, match="Unscoped personal SQL"):
        query_client.post("/query", json={"sql": "SELECT * FROM sessions", "observer": True})
