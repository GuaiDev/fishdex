"""The pin and trip-editing routes, and what /log-trip now returns about pins.

No model calls: the trip parser is replaced with a canned parse and post-log
enrichment is stubbed. Every database is temporary.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth.auth import _create_token
from src.services.pins.labels import LINK_RADIUS_M, RATING_LABELS
from src.storage.database import get_db

client = TestClient(app)
HOME = (43.5000, -79.7000)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import src.services.trip_enrichment as enrichment
    import src.services.trip_enrichment_conditions as conditions
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")
    monkeypatch.setattr(enrichment, "enrich_session", lambda *a, **k: {})
    monkeypatch.setattr(conditions, "enrich_session_conditions", lambda *a, **k: None)


@pytest.fixture()
def headers():
    return {"Authorization": f"Bearer {_create_token(get_db(), 1)}"}


def _log(headers, point=HOME, **body):
    parse = {
        "date": "2026-06-10",
        "stops": [{"location_text": "Bronte Creek", "lat": point[0], "lng": point[1]}],
    }
    with patch("src.services.trip_parser.parse_session_from_text", return_value=parse):
        return client.post("/log-trip", json={"text": "Bronte Creek.", **body}, headers=headers)


def test_labels_come_from_the_one_constants_module(headers):
    body = client.get("/pins/labels", headers=headers).json()
    assert body["link_radius_m"] == LINK_RADIUS_M == 150
    assert body["min_rated_trips_for_match"] == 3
    assert body["rating_labels"] == {str(k): v for k, v in RATING_LABELS.items()}
    assert body["rating_labels"]["1"] == "skunked"
    assert body["rating_labels"]["5"] == "best of the year"


def test_log_trip_reports_the_inferred_link_and_saves_the_timer(headers):
    pin = client.post(
        "/pins", json={"name": "Pool", "lat": HOME[0], "lng": HOME[1]}, headers=headers
    )
    assert pin.status_code == 201
    response = _log(
        headers, started_at="2026-06-10T12:00:00.000Z", ended_at="2026-06-10T14:30:00.000Z"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    (entry,) = data["pin_links"]
    assert entry["link"]["pin_id"] == pin.json()["pin"]["id"]
    assert entry["link"]["kind"] == "inferred"
    assert data["started_at"] == "2026-06-10T12:00:00+00:00"

    (session,) = client.get("/sessions", headers=headers).json()["sessions"]
    assert session["started_at"] == "2026-06-10T12:00:00+00:00"
    assert session["ended_at"] == "2026-06-10T14:30:00+00:00"
    assert session["rating"] is None
    assert session["pins"] == [{"pin_id": pin.json()["pin"]["id"], "name": "Pool", "rating": None}]


@pytest.mark.parametrize(
    "times",
    [
        {"started_at": "not a time"},
        {"started_at": "2026-06-10T12:00:00"},  # no timezone
        {"started_at": "2026-06-10T14:00:00Z", "ended_at": "2026-06-10T12:00:00Z"},
    ],
)
def test_log_trip_rejects_bad_timer_values_and_saves_nothing(headers, times):
    response = _log(headers, **times)
    assert response.status_code == 400
    assert get_db().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_a_catch_sent_without_a_count_is_stored_as_null(headers):
    response = _log(headers, catches_json=json.dumps([{"species": "creek chub"}]))
    assert response.status_code == 200
    (count,) = get_db().execute("SELECT count FROM catches").fetchone()
    assert count is None


def test_rating_and_times_can_be_edited_and_cleared(headers):
    session_id = _log(headers).json()["session_id"]
    url = f"/sessions/{session_id}"
    assert client.patch(url, json={"rating": 3}, headers=headers).json()["rating"] == 3
    edited = client.patch(
        url,
        json={"started_at": "2026-06-10T08:00:00-04:00", "ended_at": "2026-06-10T11:00:00-04:00"},
        headers=headers,
    ).json()
    assert edited["rating"] == 3
    assert edited["started_at"] == "2026-06-10T08:00:00-04:00"
    cleared = client.patch(url, json={"rating": None}, headers=headers).json()
    assert cleared["rating"] is None and cleared["started_at"] == "2026-06-10T08:00:00-04:00"


def test_editing_one_end_is_checked_against_the_stored_other(headers):
    session_id = _log(
        headers, started_at="2026-06-10T08:00:00Z", ended_at="2026-06-10T10:00:00Z"
    ).json()["session_id"]
    response = client.patch(
        f"/sessions/{session_id}", json={"started_at": "2026-06-10T11:00:00Z"}, headers=headers
    )
    assert response.status_code == 400
    assert client.get("/sessions", headers=headers).json()["sessions"][0]["started_at"] == (
        "2026-06-10T08:00:00+00:00"
    )


def test_pin_routes_edit_merge_and_delete(headers):
    a = client.post("/pins", json={"name": "A", "lat": 43.5, "lng": -79.7}, headers=headers)
    b = client.post("/pins", json={"name": "B", "lat": 43.6, "lng": -79.7}, headers=headers)
    a_id, b_id = a.json()["pin"]["id"], b.json()["pin"]["id"]

    renamed = client.patch(
        f"/pins/{a_id}",
        json={"name": "Upper A", "notes": "gate code", "is_favourite": True},
        headers=headers,
    ).json()
    assert renamed["pin"]["name"] == "Upper A" and renamed["pin"]["is_favourite"] is True
    assert client.patch(f"/pins/{a_id}", json={"name": None}, headers=headers).status_code == 422
    assert (
        client.patch(f"/pins/{a_id}", json={"notes": None}, headers=headers).json()["pin"]["notes"]
        is None
    )

    assert (
        client.post(f"/pins/{a_id}/merge", json={"into_pin_id": a_id}, headers=headers).status_code
        == 400
    )
    merged = client.post(f"/pins/{a_id}/merge", json={"into_pin_id": b_id}, headers=headers)
    assert merged.status_code == 200 and merged.json()["pin"]["is_favourite"] is True
    assert client.get(f"/pins/{a_id}", headers=headers).status_code == 404

    assert client.delete(f"/pins/{b_id}", headers=headers).json() == {"deleted": b_id}
    assert client.get("/pins", headers=headers).json()["pins"] == []


def test_pin_this_spot_and_undo(headers):
    first = _log(headers).json()
    second = _log(headers, point=(43.5005, -79.7)).json()
    assert second["pin_links"][0]["pin_prompt"] is True
    stop_id = second["pin_links"][0]["stop_id"]

    created = client.post(
        "/pins",
        json={"name": "Bend", "lat": 43.5005, "lng": -79.7, "from_stop_id": stop_id},
        headers=headers,
    ).json()
    pin_id = created["pin"]["id"]
    assert created["inferred_stop_ids"] == [first["pin_links"][0]["stop_id"]]
    assert created["facts"]["visits"] == 2

    undone = client.delete(
        f"/pins/{pin_id}/links/{first['pin_links'][0]['stop_id']}", headers=headers
    )
    assert undone.status_code == 200
    assert client.get(f"/pins/{pin_id}", headers=headers).json()["facts"]["visits"] == 1


def test_recalled_history_route_marks_and_excludes(headers):
    pin_id = client.post(
        "/pins", json={"name": "Old haunt", "lat": 44.0, "lng": -80.0}, headers=headers
    ).json()["pin"]["id"]
    added = client.post(
        f"/pins/{pin_id}/recalled",
        json={"date_approx": "summer 2019", "species": ["brook trout"], "rating": 5},
        headers=headers,
    )
    assert added.status_code == 201 and added.json()["provenance"] == "recalled"
    detail = client.get(f"/pins/{pin_id}", headers=headers).json()
    assert detail["pin"]["state"] == "visited"
    assert detail["facts"]["visits"] == 0 and detail["facts"]["species"] == []
    assert detail["recalled"][0]["species"] == ["brook trout"]
    rid = added.json()["id"]
    assert client.delete(f"/pins/{pin_id}/recalled/{rid}", headers=headers).status_code == 200
    assert client.get(f"/pins/{pin_id}", headers=headers).json()["pin"]["state"] == "saved"


def test_photo_log_with_bad_times_is_refused_before_the_photo_is_saved(
    headers, tmp_path, monkeypatch
):
    import io

    from PIL import Image

    from src.services import photo_storage

    photos = tmp_path / "photos"
    monkeypatch.setattr(photo_storage, "PHOTOS_DIR", photos)
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    response = client.post(
        "/log-trip/photo",
        data={"text": "Bronte Creek.", "started_at": "2026-06-10T12:00:00"},
        files={"photo": ("fish.jpg", buf.getvalue(), "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 400
    assert not photos.exists() or not any(photos.iterdir())
