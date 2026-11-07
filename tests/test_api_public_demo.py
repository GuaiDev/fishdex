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

HOME_BBOX = "-80,43,-79,44"

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


def _seed_spots():
    from src.models.spot import Spot
    from src.storage.spots import replace_spots

    def spot(tier, lat, lng, sid):
        return Spot(
            spot_id=sid,
            kind="river_mouth",
            jurisdiction="CA-ON",
            lat=lat,
            lng=lng,
            title=f"Spot {sid}",
            tier=tier,
            structure_type="lake_mouth",
            built_at="2026-01-01T00:00:00+00:00",
        )

    replace_spots(
        get_db(),
        "CA-ON",
        [
            spot(1, 43.5, -79.5, "river_mouth:n795000_435000"),
            spot(2, 43.6, -79.6, "river_mouth:n796000_436000"),
            spot(3, 43.7, -79.7, "river_mouth:n797000_437000"),
            spot(1, 46.0, -75.0, "river_mouth:n750000_460000"),
        ],
    )


def _ids(r):
    return {f["properties"]["spot_id"] for f in r.json()["features"]}


def test_demo_spots_are_tier_one_even_when_more_is_asked_for():
    _seed_spots()
    for tier in (1, 2, 3):
        r = client.get(f"/map/spots?tier={tier}&bbox={HOME_BBOX}")
        assert _ids(r) == {"river_mouth:n795000_435000"}


def test_signed_in_callers_still_get_every_tier():
    _seed_spots()
    token = _create_token(get_db(), 1)
    r = client.get(
        f"/map/spots?tier=3&bbox={HOME_BBOX}", headers={"Authorization": f"Bearer {token}"}
    )
    assert _ids(r) == {
        "river_mouth:n795000_435000",
        "river_mouth:n796000_436000",
        "river_mouth:n797000_437000",
    }


def test_demo_spots_are_limited_to_a_modest_area():
    _seed_spots()
    huge = client.get("/map/spots?tier=1&bbox=-85,38,-74,49")
    assert "river_mouth:n750000_460000" not in _ids(huge)
    assert "river_mouth:n795000_435000" in _ids(huge)
    none = client.get("/map/spots?tier=1")
    assert _ids(none) == {"river_mouth:n795000_435000"}
    token = _create_token(get_db(), 1)
    full = client.get(
        "/map/spots?tier=1&bbox=-90,40,-70,50", headers={"Authorization": f"Bearer {token}"}
    )
    assert "river_mouth:n750000_460000" in _ids(full)


def test_demo_cannot_open_a_spot_above_tier_one():
    _seed_spots()
    assert client.get("/map/spots/river_mouth:n796000_436000").status_code == 404
    assert client.get("/map/spots/river_mouth:n795000_435000").status_code == 200
    token = _create_token(get_db(), 1)
    r = client.get(
        "/map/spots/river_mouth:n796000_436000", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200


def test_demo_section_tier_is_clamped_to_one(monkeypatch):
    import src.services.sections as sections

    seen = []

    def fake_geojson(db, user_id, bbox=None, types=None, jurisdiction=None, max_tier=2):
        seen.append(("list", bbox, max_tier))
        return {"type": "FeatureCollection", "features": []}

    def fake_detail(db, section_id, user_id, max_tier=2):
        seen.append(("detail", None, max_tier))
        return {"section_id": section_id}

    monkeypatch.setattr(sections, "sections_geojson", fake_geojson)
    monkeypatch.setattr(sections, "section_detail", fake_detail)
    client.get("/map/sections?tier=3&bbox=-90,40,-70,50")
    client.get("/map/sections/abc?tier=3")
    assert seen[0][2] == 1
    assert seen[0][1][2] - seen[0][1][0] <= 1.0
    assert seen[1][2] == 1

    seen.clear()
    token = _create_token(get_db(), 1)
    auth = {"Authorization": f"Bearer {token}"}
    client.get("/map/sections?tier=3&bbox=-90,40,-70,50", headers=auth)
    client.get("/map/sections/abc?tier=3", headers=auth)
    assert seen[0][2] == 3 and seen[0][1] == (-90.0, 40.0, -70.0, 50.0)
    assert seen[1][2] == 3
