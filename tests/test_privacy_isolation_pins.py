"""One angler's pins, links, ratings and recalled history never reach another.

Companion to test_privacy_isolation.py, driven through the HTTP routes the
web app uses. Every route takes the user from the bearer token; another
user's pin, stop or trip must answer 404 exactly as a missing one does, and
must come out of the attempt unchanged. No model calls; all databases are
temporary.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import _create_token
from src.services.trip_logger import log_session
from src.storage import pins as store
from src.storage.database import get_db

client = TestClient(app)

A_SPOT = (43.5000, -79.7000)
B_SPOT = (43.6000, -79.6000)
MISSING = 999_999


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import src.services.trip_enrichment as enrichment
    import src.services.trip_enrichment_conditions as conditions
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")
    monkeypatch.setattr(enrichment, "enrich_session", lambda *a, **k: {})
    monkeypatch.setattr(conditions, "enrich_session_conditions", lambda *a, **k: None)


@pytest.fixture()
def world():
    """Two users, each with one pin, one linked trip and one recalled record."""
    db = get_db()
    db["users"].insert({"id": 2, "username": "alice", "role": "beta"})
    db["users"].insert({"id": 3, "username": "bob", "role": "beta"})
    out = {"db": db}
    for name, uid, spot in (("a", 2, A_SPOT), ("b", 3, B_SPOT)):
        headers = {"Authorization": f"Bearer {_create_token(db, uid)}"}
        pin = client.post(
            "/pins",
            json={"name": f"{name}'s secret run", "lat": spot[0], "lng": spot[1], "notes": "x"},
            headers=headers,
        ).json()["pin"]
        logged = log_session(
            {
                "date": "2026-06-10",
                "stops": [{"location_text": "creek", "lat": spot[0], "lng": spot[1]}],
            },
            db,
            user_id=uid,
            structured_catches=[{"species": "brook trout", "count": 2}],
        )
        client.patch(f"/sessions/{logged['session_id']}", json={"rating": 5}, headers=headers)
        client.post(
            f"/pins/{pin['id']}/recalled",
            json={"date_approx": "2019", "species": ["redside dace"], "rating": 4},
            headers=headers,
        )
        out[name] = {
            "uid": uid,
            "headers": headers,
            "pin_id": pin["id"],
            "session_id": logged["session_id"],
            "stop_id": logged["pin_links"][0]["stop_id"],
        }
    return out


def _snapshot(db) -> list:
    return [
        db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("pins", "pin_links", "pin_recalled_visits", "sessions", "stops", "catches")
    ]


def test_each_user_lists_only_their_own_pins(world):
    for me, other in (("a", "b"), ("b", "a")):
        body = client.get("/pins", headers=world[me]["headers"]).json()
        assert [p["id"] for p in body["pins"]] == [world[me]["pin_id"]]
        assert "secret run" in body["pins"][0]["name"]
        assert world[other]["pin_id"] not in [p["id"] for p in body["pins"]]


def test_reading_another_users_pin_is_404_like_a_missing_one(world):
    b = world["b"]["headers"]
    theirs = client.get(f"/pins/{world['a']['pin_id']}", headers=b)
    missing = client.get(f"/pins/{MISSING}", headers=b)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


def test_own_pin_shows_own_trips_ratings_and_recalled_only(world):
    detail = client.get(f"/pins/{world['a']['pin_id']}", headers=world["a"]["headers"]).json()
    assert [t["session_id"] for t in detail["trips"]] == [world["a"]["session_id"]]
    assert detail["trips"][0]["rating"] == 5
    assert detail["facts"]["fish_logged"] == 2
    assert [r["provenance"] for r in detail["recalled"]] == ["recalled"]


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("get", "/pins/{a_pin}", None),
        ("patch", "/pins/{a_pin}", {"name": "mine now", "is_favourite": True}),
        ("delete", "/pins/{a_pin}", None),
        ("post", "/pins/{a_pin}/merge", {"into_pin_id": "{b_pin}"}),
        ("post", "/pins/{b_pin}/merge", {"into_pin_id": "{a_pin}"}),
        ("post", "/pins/{a_pin}/links", {"stop_id": "{b_stop}"}),
        ("post", "/pins/{b_pin}/links", {"stop_id": "{a_stop}"}),
        ("delete", "/pins/{a_pin}/links/{a_stop}", None),
        ("post", "/pins/{a_pin}/recalled", {"date_approx": "2020", "species": ["x"]}),
        ("delete", "/pins/{a_pin}/recalled/1", None),
        ("patch", "/sessions/{a_session}", {"rating": 1}),
        ("patch", "/sessions/{a_session}", {"started_at": "2026-06-10T08:00:00+00:00"}),
        ("put", "/sessions/{a_session}/pins/{a_pin}/rating", {"rating": 1}),
        ("put", "/sessions/{b_session}/pins/{a_pin}/rating", {"rating": 1}),
        ("put", "/sessions/{a_session}/pins/{b_pin}/rating", {"rating": 1}),
        ("post", "/pins", {"name": "steal", "lat": 43.5, "lng": -79.7, "from_stop_id": "{a_stop}"}),
    ],
)
def test_user_b_cannot_read_or_change_anything_of_user_a(world, method, path, body):
    ids = {
        "a_pin": world["a"]["pin_id"],
        "b_pin": world["b"]["pin_id"],
        "a_stop": world["a"]["stop_id"],
        "b_stop": world["b"]["stop_id"],
        "a_session": world["a"]["session_id"],
        "b_session": world["b"]["session_id"],
    }
    url = path.format(**ids)
    payload = None
    if body is not None:
        payload = {
            k: (int(v.format(**ids)) if isinstance(v, str) and v.startswith("{") else v)
            for k, v in body.items()
        }
    pins_before = [
        p["id"] for p in client.get("/pins", headers=world["b"]["headers"]).json()["pins"]
    ]
    before = _snapshot(world["db"])

    response = client.request(method.upper(), url, json=payload, headers=world["b"]["headers"])

    assert response.status_code == 404, response.text
    if not (method == "post" and path == "/pins"):
        assert _snapshot(world["db"]) == before
    else:
        # The refused create left no pin behind for B either.
        after = [p["id"] for p in client.get("/pins", headers=world["b"]["headers"]).json()["pins"]]
        assert after == pins_before
        assert _snapshot(world["db"]) == before


def test_another_users_trip_never_links_to_my_pin_by_proximity(world):
    """B logs a trip right on A's pin: it must not link to A's pin or prompt from A's stops."""
    db = world["db"]
    logged = log_session(
        {
            "date": "2026-06-11",
            "stops": [{"location_text": "c", "lat": A_SPOT[0], "lng": A_SPOT[1]}],
        },
        db,
        user_id=world["b"]["uid"],
    )
    (entry,) = logged["pin_links"]
    assert entry["link"] is None and entry["candidates"] == []
    assert entry["pin_prompt"] is False  # A's unpinned stops are not B's history
    assert store.link_for_stop(db, world["b"]["uid"], entry["stop_id"]) is None


def test_my_new_pin_never_back_links_another_users_stops(world):
    db = world["db"]
    store.unlink_stop(db, world["a"]["uid"], world["a"]["pin_id"], world["a"]["stop_id"])
    created = client.post(
        "/pins",
        json={"name": "on top of A", "lat": A_SPOT[0], "lng": A_SPOT[1]},
        headers=world["b"]["headers"],
    ).json()
    assert created["inferred_stop_ids"] == []
    assert store.link_for_stop(db, world["a"]["uid"], world["a"]["stop_id"]) is None


def test_another_users_trips_never_appear_in_sessions_or_unpinned_stops(world):
    b = world["b"]["headers"]
    sessions = client.get("/sessions", headers=b).json()["sessions"]
    assert [s["id"] for s in sessions] == [world["b"]["session_id"]]
    assert [p["pin_id"] for p in sessions[0]["pins"]] == [world["b"]["pin_id"]]
    store.unlink_stop(world["db"], world["a"]["uid"], world["a"]["pin_id"], world["a"]["stop_id"])
    unpinned = client.get("/pins", headers=b).json()["unpinned_stops"]
    assert world["a"]["stop_id"] not in [s["stop_id"] for s in unpinned]


def test_pin_routes_require_a_user(world):
    assert client.get("/pins").status_code == 401
    assert client.get(f"/pins/{world['a']['pin_id']}").status_code == 401
    assert client.get("/pins/labels").status_code == 401
