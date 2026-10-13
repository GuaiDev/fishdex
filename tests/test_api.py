"""FastAPI endpoint tests.

Tests that call /chat and /log-trip require a funded Anthropic API key
(those endpoints run the full agentic loop). They are marked accordingly
and will be skipped when credentials are unavailable.
"""

import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)

REQUIRES_API = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Redirect every endpoint's get_db() call to a throwaway file.

    Endpoints call get_db() with no path argument, which resolves to the
    real data/fishing.db (or DATA_DIR/fishing.db) unless DB_PATH is patched.
    Without this, any write-endpoint test — including the @REQUIRES_API ones,
    which only skip locally because no ANTHROPIC_API_KEY is set — silently
    inserts rows into the real production database. Per CLAUDE.md, tests
    must never touch real user data.
    """
    import src.storage.database as database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_no_auth_required():
    response = client.get("/health")
    assert response.status_code == 200


@REQUIRES_API
def test_chat_basic():
    response = client.post(
        "/chat",
        json={
            "message": "What fish can I catch near Oakville Ontario?",
            "conversation_history": [],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert len(data["reply"]) > 0
    assert "conversation_history" in data


@REQUIRES_API
def test_chat_maintains_history():
    r1 = client.post(
        "/chat",
        json={
            "message": "I'm planning to fish the Credit River",
            "conversation_history": [],
        },
    )
    assert r1.status_code == 200
    history = r1.json()["conversation_history"]
    assert len(history) >= 2

    r2 = client.post(
        "/chat",
        json={
            "message": "What species should I target there?",
            "conversation_history": history,
        },
    )
    assert r2.status_code == 200


@REQUIRES_API
def test_log_trip_endpoint():
    response = client.post(
        "/log-trip",
        json={"text": "Fished Bronte Creek today, caught a creek chub on worm."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged"
    assert "session_id" in data


def test_ingest_data_requires_lat_lng(monkeypatch):
    """Missing lat/lng returns 400."""
    monkeypatch.setenv("FISHBOT_API_KEY", "test-key")
    response = client.post(
        "/ingest/data",
        json={"label": "test"},
        headers={"X-Api-Key": "test-key"},
    )
    # Either 400 (missing lat/lng), 401 (wrong key), or 422 (validation) — all acceptable
    assert response.status_code in (400, 401, 422)


def test_api_key_unset_refuses(monkeypatch):
    """With FISHBOT_API_KEY unset, protected endpoints refuse (503), even with a header."""
    monkeypatch.delenv("FISHBOT_API_KEY", raising=False)
    response = client.post(
        "/ingest/data",
        json={"lat": 43.45, "lng": -79.72, "radius_km": 1},
        headers={"X-Api-Key": "anything"},
    )
    assert response.status_code == 503


def test_api_key_empty_refuses(monkeypatch):
    monkeypatch.setenv("FISHBOT_API_KEY", "")
    response = client.post("/ingest/data", json={"lat": 43.45, "lng": -79.72, "radius_km": 1})
    assert response.status_code == 503


def test_api_key_wrong_refuses(monkeypatch):
    monkeypatch.setenv("FISHBOT_API_KEY", "right-key")
    response = client.post(
        "/ingest/data",
        json={"lat": 43.45, "lng": -79.72, "radius_km": 1},
        headers={"X-Api-Key": "wrong-key"},
    )
    assert response.status_code == 401
    response = client.post("/ingest/data", json={"lat": 43.45, "lng": -79.72, "radius_km": 1})
    assert response.status_code == 401


def test_api_key_right_passes(monkeypatch):
    monkeypatch.setenv("FISHBOT_API_KEY", "right-key")
    response = client.post(
        "/ingest/data",
        json={"label": "test"},
        headers={"X-Api-Key": "right-key"},
    )
    # Past the key check: the body is rejected by validation, not by auth.
    assert response.status_code in (400, 422)


def test_admin_token_refuses_without_key(monkeypatch):
    monkeypatch.delenv("FISHBOT_API_KEY", raising=False)
    assert client.post("/admin/token").status_code == 503
    monkeypatch.setenv("FISHBOT_API_KEY", "right-key")
    assert client.post("/admin/token").status_code == 401
    assert client.post("/admin/token", headers={"X-Api-Key": "bad"}).status_code == 401


def _apikey_headers(monkeypatch):
    monkeypatch.setenv("FISHBOT_API_KEY", "test-key")
    return {"X-Api-Key": "test-key"}


def test_log_trip_photo_rejects_unsupported_content_type(monkeypatch):
    headers = _apikey_headers(monkeypatch)
    response = client.post(
        "/log-trip/photo",
        data={"text": "Fished Bronte Creek, caught a creek chub"},
        files={"photo": ("catch.pdf", b"not an image", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 400


def test_log_trip_photo_requires_text_field(monkeypatch):
    import io

    from PIL import Image

    headers = _apikey_headers(monkeypatch)
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    response = client.post(
        "/log-trip/photo",
        files={"photo": ("catch.jpg", buf.getvalue(), "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 422


def test_log_trip_photo_saves_file_and_logs_session(monkeypatch, tmp_path):
    import io
    from unittest.mock import patch

    from PIL import Image

    from src.services import photo_storage

    monkeypatch.setattr(photo_storage, "PHOTOS_DIR", tmp_path / "photos")
    headers = _apikey_headers(monkeypatch)

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(60, 110, 70)).save(buf, format="JPEG")

    canned_parse = {
        "date": "2026-06-01",
        "stops": [{
            "location_text": "Bronte Creek",
            "species_caught": ["creek chub"],
        }],
    }
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse):
        response = client.post(
            "/log-trip/photo",
            data={"text": "Fished Bronte Creek, caught a creek chub"},
            files={"photo": ("catch.jpg", buf.getvalue(), "image/jpeg")},
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged"
    assert data["photo_url"].startswith("/photos/")
    assert (tmp_path / "photos").exists()


@REQUIRES_API
def test_log_trip_with_photo_metadata():
    """Photo GPS and timestamp should be accepted and reflected in response."""
    response = client.post(
        "/log-trip",
        json={
            "text": "Fished Bronte Creek, caught a creek chub",
            "photo_lat": 43.45,
            "photo_lng": -79.72,
            "photo_taken_at": "2025-06-14T08:30:00",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged"
    assert data["stops_logged"] >= 1


def test_log_trip_json_with_catches_json_persists_structured_fields(monkeypatch):
    """The multi-catch UI's catches_json field must actually persist
    count/size/bait, not just get silently accepted and ignored."""
    import json
    from unittest.mock import patch

    from src.storage.catches import get_catches_for_session
    from src.storage.database import get_db

    canned_parse = {
        "date": "2026-06-01",
        "stops": [{
            "location_text": "Bronte Creek",
            "species_caught": ["smallmouth bass", "rock bass"],
        }],
    }
    catches_json = json.dumps([
        {"species": "smallmouth bass", "count": 2, "biggest_size_cm": 35.0, "bait": "spinnerbait"},
    ])
    headers = _apikey_headers(monkeypatch)
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse):
        response = client.post(
            "/log-trip",
            json={
                "text": "Caught 2 smallmouth bass on spinnerbait and a rock bass.",
                "catches_json": catches_json,
            },
            headers=headers,
        )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    db = get_db()
    catches = {c["species"]: c for c in get_catches_for_session(db, session_id)}
    assert catches["smallmouth bass"]["count"] == 2
    assert catches["smallmouth bass"]["biggest_size_cm"] == 35.0
    assert catches["smallmouth bass"]["bait"] == "spinnerbait"
    # rock bass wasn't in catches_json — untouched, old NL-only behavior.
    assert catches["rock bass"]["count"] is None


def test_log_trip_json_with_fast_tally_catch_persists_as_unidentified(monkeypatch):
    """A bare '+1 fish' tap sent through catches_json (no species, no photo,
    source='fast_tally') must persist as its own confirmed-but-unidentified
    catch row, with caught_at preserved — the one true end-to-end path
    through _normalize_structured_catches -> log_session -> DB."""
    import json
    from unittest.mock import patch

    from src.storage.catches import get_catches_for_session
    from src.storage.database import get_db

    canned_parse = {
        "date": "2026-07-19",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    catches_json = json.dumps([
        {"source": "fast_tally", "count": 1, "caught_at": "2026-07-19T14:03:00"},
    ])
    headers = _apikey_headers(monkeypatch)
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse):
        response = client.post(
            "/log-trip",
            json={"text": "Fishing trip.", "catches_json": catches_json},
            headers=headers,
        )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    db = get_db()
    catches = get_catches_for_session(db, session_id)
    assert len(catches) == 1
    assert catches[0]["species"] == "unidentified sp."
    assert catches[0]["species_confirmed"] == 1
    assert catches[0]["caught_at"] == "2026-07-19T14:03:00"
    assert response.json()["pending_catches"] == []


def test_log_trip_response_includes_summary_card_fields(monkeypatch):
    """The end-of-session summary card reads location_name/conditions/catches
    off the /log-trip(/photo) response — must actually be present, not just
    silently absent because nothing wires them through."""
    import json
    from unittest.mock import patch

    canned_parse = {
        "date": "2026-07-20",
        "stops": [{
            "location_text": "Sixteen Mile Creek",
            "location_name": "Sixteen Mile Creek",
            "species_caught": [],
        }],
    }
    catches_json = json.dumps([
        {"species": "smallmouth bass", "count": 1, "biggest_size_cm": 35.0, "bait": "spinnerbait"},
        {"source": "fast_tally", "count": 1, "caught_at": "2026-07-20T14:00:00"},
    ])
    headers = _apikey_headers(monkeypatch)
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse):
        response = client.post(
            "/log-trip",
            json={"text": "Sixteen Mile Creek.", "catches_json": catches_json},
            headers=headers,
        )
    assert response.status_code == 200
    data = response.json()
    assert data["location_name"] == "Sixteen Mile Creek"
    # No lat/lng in the canned parse, so condition enrichment never ran —
    # must be an honest None, not a missing key.
    assert data["conditions"] is None

    assert len(data["catches"]) == 2
    by_species = {c["species"]: c for c in data["catches"]}
    assert by_species["smallmouth bass"]["is_new_pb"] is True
    assert by_species["smallmouth bass"]["biggest_size_cm"] == 35.0
    assert by_species["unidentified sp."]["is_new_pb"] is False
    assert by_species["unidentified sp."]["biggest_size_cm"] is None


def test_confirm_species_endpoint_returns_is_new_pb(monkeypatch, tmp_path):
    """The summary card's PB flag depends on this — a "let vision suggest
    it" detailed catch (no typed species) doesn't know its real species
    until this call, so is_new_pb can only be reported here, not at
    insert time. Mirrors the live E2E finding: vision guessed "warmouth"
    at insert (which trivially became that placeholder's first-ever PB),
    then the user corrected it to "smallmouth bass" — a species with no
    prior record — via this endpoint."""
    import io
    from unittest.mock import patch

    from PIL import Image

    from src.auth.auth import _create_token
    from src.services import photo_storage
    from src.storage.database import get_db

    monkeypatch.setattr(photo_storage, "PHOTOS_DIR", tmp_path / "photos")
    headers = _apikey_headers(monkeypatch)

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(60, 110, 70)).save(buf, format="JPEG")

    canned_parse = {
        "date": "2026-07-20",
        "stops": [{"location_text": "Sixteen Mile Creek", "species_caught": []}],
    }
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse), \
         patch("src.services.trip_logger._photo_species_candidates", return_value={
             "screened": True, "unresolved": False, "note": None,
             "candidates": [{"species": "warmouth", "confidence": "medium"}],
         }):
        response = client.post(
            "/log-trip/photo",
            data={"text": "Sixteen Mile Creek.", "catches_json": '[{"biggest_size_cm": 40.0}]'},
            files={"photo": ("catch.jpg", buf.getvalue(), "image/jpeg")},
            headers=headers,
        )
    assert response.status_code == 200
    data = response.json()
    catch_id = data["pending_catches"][0]["catch_id"]
    # Insert-time provisional species is the vision guess, not the real ID.
    assert data["catches"][0]["species"] == "warmouth"

    # confirm-species requires a real Bearer token (no X-Api-Key fallback,
    # unlike /log-trip) — mint one for user_id=1, the same admin/apikey
    # fallback user the catch above was inserted under.
    token = _create_token(get_db(), user_id=1)
    confirm_response = client.post(
        f"/catches/{catch_id}/confirm-species",
        json={"species": "smallmouth bass"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["is_new_pb"] is True


def test_log_trip_malformed_catches_json_degrades_gracefully(monkeypatch):
    """Garbage catches_json must not 500 the request — falls back to
    text-only logging, same as if the field were absent."""
    from unittest.mock import patch

    canned_parse = {
        "date": "2026-06-01",
        "stops": [{"location_text": "Bronte Creek", "species_caught": ["creek chub"]}],
    }
    headers = _apikey_headers(monkeypatch)
    with patch("src.services.trip_parser.parse_session_from_text", return_value=canned_parse):
        response = client.post(
            "/log-trip",
            json={"text": "Fished Bronte Creek, caught a creek chub", "catches_json": "not json"},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["status"] == "logged"


def test_log_page_served():
    """GET /log returns the mobile trip logging page."""
    response = client.get("/log")
    assert response.status_code == 200
    assert "FishBot" in response.text
