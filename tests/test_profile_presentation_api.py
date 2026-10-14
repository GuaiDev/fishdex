"""Presentation prominence and the profile API: structure only, nothing locked out,
everything scoped to the logged-in user."""

import shutil

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import generate_invite_code, redeem_invite_code
from src.services import presentation as pres
from src.services.context import profile as prof
from src.storage import angler_profile as store
from src.storage import database
from tests.test_angler_profile import spot, trip

# ── presentation ─────────────────────────────────────────────────────────────


def _profile(db, user_id, orientation=None, confidence=None):
    p = prof.derive_profile(db, user_id)
    if orientation is not None:
        p.orientation.value, p.orientation.confidence = orientation, confidence
    return p


def test_presentation_never_locks_anything_out(db):
    for o in (0.0, 0.5, 1.0):
        out = pres.presentation(_profile(db, 1, o, 1.0))
        for section in ("dex", "pins"):
            assert all(v >= pres.PROMINENCE_FLOOR for v in out[section].values())
        assert all(d["available"] for d in out["log_form"]["details"].values())
        assert out["map"]["more_detail_toggle"] is True
        assert set(out["log_form"]["defaults"]) == {"species", "place", "photo", "trip_rating"}


def test_lifelist_elements_rise_toward_the_lifelister_end_and_specialist_ones_fall(db):
    spec = pres.presentation(_profile(db, 1, 0.0, 1.0))["dex"]
    life = pres.presentation(_profile(db, 1, 1.0, 1.0))["dex"]
    mid = pres.presentation(_profile(db, 1, 0.5, 1.0))["dex"]
    assert life["family_completion"] > spec["family_completion"]
    assert spec["seasonal_timeline"] > life["seasonal_timeline"]
    assert mid["journal_timeline"] > life["journal_timeline"]
    assert mid["journal_timeline"] > spec["journal_timeline"]


def test_low_confidence_presents_like_the_middle(db):
    unsure = pres.presentation(_profile(db, 1, 1.0, 0.05))["dex"]
    mid = pres.presentation(_profile(db, 1, 0.5, 1.0))["dex"]
    assert abs(unsure["family_completion"] - mid["family_completion"]) < 0.05


def test_label_wording_follows_each_concept_and_water_mix_never_does(db):
    spot(db, "creek_junction", 43.0, -80.0)
    for k in range(3):
        trip(db, 1, f"2025-06-0{k + 1}", 43.001, -80.001, [])
    knows = pres.presentation(prof.derive_profile(db, 1))["map"]
    new = pres.presentation(prof.derive_profile(db, 2))["map"]
    assert knows["labels"]["confluence"]["label_mode"] == "term"
    assert new["labels"]["confluence"]["label_mode"] == "plain"
    assert knows["default_layer_density"] > new["default_layer_density"]
    assert knows["water_body_mix"] == new["water_body_mix"] == pres.WATER_BODY_MIX


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, _template_db):
    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    handle = database.get_db()

    def login(name):
        got = redeem_invite_code(handle, generate_invite_code(handle), name)
        return {"id": got["user_id"], "h": {"Authorization": f"Bearer {got['token']}"}}

    class Api:
        client = TestClient(app)
        db = handle
        ann = login("ann")
        bob = login("bob")

    return Api()


def test_every_profile_route_needs_a_login(api):
    c = api.client
    for method, path in (
        ("get", "/profile"),
        ("get", "/profile/presentation"),
        ("get", "/terms"),
        ("post", "/profile/concept-events"),
        ("put", "/profile/declared/orientation"),
        ("delete", "/profile/declared/orientation"),
    ):
        assert getattr(c, method)(path).status_code in (401, 403, 422), path


def test_declared_values_and_events_through_the_api_stay_with_their_user(api):
    c, ann, bob = api.client, api.ann, api.bob
    r = c.put("/profile/declared/orientation", json={"value": 0.9, "override": True},
              headers=ann["h"])  # fmt: skip
    assert r.status_code == 200, r.text
    assert c.post("/profile/concept-events", json={"term_id": "dam", "kind": "definition_opened"},
                  headers=ann["h"]).status_code == 201  # fmt: skip
    assert c.get("/profile", headers=ann["h"]).json()["orientation"]["value"] == 0.9
    assert c.get("/profile", headers=bob["h"]).json()["orientation"]["value"] == 0.5
    assert store.event_counts(api.db, bob["id"]) == {}
    assert c.delete("/profile/declared/orientation", headers=bob["h"]).status_code == 404
    assert c.delete("/profile/declared/orientation", headers=ann["h"]).status_code == 200


def test_the_api_refuses_a_skill_field_and_bad_values(api):
    c, h = api.client, api.ann["h"]
    assert c.put("/profile/declared/skill_level", json={"value": 0.5}, headers=h).status_code == 400
    assert c.put("/profile/declared/orientation", json={"value": 2}, headers=h).status_code == 400
    for bad in (1e9, -1):
        r = c.put("/profile/declared/orientation", json={"value": 0.5, "weight": bad}, headers=h)
        assert r.status_code == 400
    r = c.post("/profile/concept-events", json={"term_id": "dam", "kind": "x"}, headers=h)
    assert r.status_code == 400


def test_presentation_and_terms_endpoints_serve_structure_only(api):
    c, h = api.client, api.ann["h"]
    out = c.get("/profile/presentation", headers=h).json()
    assert set(out) >= {"map", "dex", "log_form", "pins", "basis"}
    assert out["map"]["labels"]["confluence"]["label_mode"] == "plain"
    terms_out = c.get("/terms", headers=h).json()["terms"]
    assert {t["term_id"] for t in terms_out} >= {"dam", "confluence", "weed_edge"}


def test_the_recorded_example_still_matches_the_models_and_the_presentation():
    """tests/fixtures/profile_api_responses.json is user 1's real profile after replaying
    their logged trips; the UI is built against it, so it must stay valid."""
    import json
    from pathlib import Path

    from src.models.angler_profile import AnglerProfile

    path = Path(__file__).parent / "fixtures" / "profile_api_responses.json"
    entries = {e["request"]: e for e in json.loads(path.read_text())["requests"]}
    profile = AnglerProfile.model_validate(entries["GET /profile"]["response"])
    assert pres.presentation(profile) == entries["GET /profile/presentation"]["response"]
    assert entries["PUT /profile/declared/skill_level"]["status"] == 400
