"""The public read-only demo: which routes open without a token, and which must not.

No model calls; every database is temporary.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import _create_token
from src.storage.database import get_db

client = TestClient(app)

PUBLIC_GETS = [
    "/map/spots/labels",
    "/map/spots?tier=2",
    "/map/sections?tier=2",
]

# Anything personal, model-spending, writing or admin must still need a sign-in.
PROTECTED = [
    ("get", "/auth/me"),
    ("post", "/chat"),
    ("post", "/log-trip"),
    ("get", "/sessions"),
    ("get", "/fishdex"),
    ("get", "/profile"),
    ("get", "/pins"),
    ("get", "/map/my-stops"),
    ("get", "/map/weed-reports"),
    ("post", "/map/weed-reports"),
    ("get", "/map/water-bodies"),
    ("get", "/terms"),
    ("post", "/admin/invite"),
    ("get", "/admin/users"),
    ("get", "/admin/invites"),
    ("post", "/admin/token"),
    ("get", "/admin/db-stats"),
    ("post", "/ingest"),
]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")
    monkeypatch.delenv("FISHBOT_PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("FISHBOT_API_KEY", "right-key")


@pytest.mark.parametrize("path", PUBLIC_GETS)
def test_demo_routes_open_without_a_token(path):
    assert client.get(path).status_code == 200


def test_unknown_spot_is_a_404_not_a_401():
    assert client.get("/map/spots/nope").status_code == 404
    assert client.get("/map/sections/nope").status_code == 404


@pytest.mark.parametrize("method,path", PROTECTED)
def test_everything_else_stays_protected(method, path):
    assert getattr(client, method)(path).status_code in (401, 503)


def test_new_to_me_is_personal_and_needs_a_sign_in():
    r = client.get("/map/spots?bbox=-80,43,-79,44&new_to_me=true")
    assert r.status_code == 401


def test_a_bad_token_is_still_refused_not_treated_as_demo():
    r = client.get("/map/spots", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_a_real_token_still_works_on_demo_routes():
    token = _create_token(get_db(), 1)
    r = client.get("/map/spots", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    r = client.get(
        "/map/spots?bbox=-80,43,-79,44&new_to_me=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_demo_is_not_reached_by_a_wrong_api_key():
    r = client.post("/admin/token", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_demo_can_be_switched_off(monkeypatch, value):
    monkeypatch.setenv("FISHBOT_PUBLIC_DEMO", value)
    for path in PUBLIC_GETS:
        assert client.get(path).status_code == 401
