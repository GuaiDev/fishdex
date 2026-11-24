"""API defaults come from the signed-in user's home, or from the deploy setting."""

import importlib
import types
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src import settings
from src.api.main import _demo_limits, app
from src.auth.auth import create_session
from src.models.profile import Location
from src.models.user_context import UserContext
from src.storage.database import get_db
from src.storage.profile import set_user_home

client = TestClient(app)
GUELPH = Location(name="Guelph, ON", lat=43.55, lng=-80.25)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import src.storage.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test_fishing.db")


def _signed_in(username, home, jurisdiction):
    db = get_db()
    uid = db["users"].insert({"username": username}).last_pk
    if jurisdiction:
        set_user_home(db, uid, home, jurisdiction)
    return {"Authorization": f"Bearer {create_session(db, uid)}"}


def _capture_fishdex(monkeypatch):
    seen = []
    import src.services.fishdex as fishdex

    def fake(db, user_id, jurisdiction):
        seen.append(jurisdiction)
        return {}

    monkeypatch.setattr(fishdex, "get_fishdex_data", fake)
    return seen


def test_fishdex_uses_the_users_home_jurisdiction(monkeypatch):
    seen = _capture_fishdex(monkeypatch)
    assert client.get("/fishdex", headers=_signed_in("g", GUELPH, "CA-ON")).status_code == 200
    assert client.get("/fishdex", headers=_signed_in("b", None, "CA-BC")).status_code == 200
    assert seen == ["CA-ON", "CA-BC"]


def test_fishdex_without_a_home_uses_the_deploy_default(monkeypatch):
    seen = _capture_fishdex(monkeypatch)
    monkeypatch.setenv("FISHDEX_DEFAULT_JURISDICTION", "US-MI")
    assert client.get("/fishdex", headers=_signed_in("n", None, None)).status_code == 200
    assert seen == ["US-MI"]


def test_conditions_fall_back_to_the_users_home(monkeypatch):
    weather = importlib.import_module("src.ingest.global.weather")
    calls = []

    def conditions(lat, lng):
        calls.append((lat, lng))
        return types.SimpleNamespace(
            time=datetime(2026, 1, 1),
            temperature_c=1,
            humidity_pct=1,
            wind_speed_kmh=1,
            cloud_cover_pct=1,
            weather_code=1,
            pressure_hpa=1000,
        )

    monkeypatch.setattr(weather, "get_current_conditions", conditions)
    monkeypatch.setattr(
        weather,
        "compute_pressure_trend",
        lambda lat, lng: types.SimpleNamespace(trend="steady", delta_24h_hpa=0.0),
    )
    body = client.get("/conditions", headers=_signed_in("g", GUELPH, "CA-ON")).json()
    assert body["location"]["source"] == "home"
    assert body["location"]["name"] == "Guelph, ON"
    assert calls == [(GUELPH.lat, GUELPH.lng)]

    body = client.get("/conditions", headers=_signed_in("n", None, None)).json()
    assert body["location"]["source"] == "regional_default"


def test_demo_centre_is_a_deploy_setting(monkeypatch):
    monkeypatch.setenv("FISHDEX_DEMO_CENTER", "-80.25,43.55")
    box, _ = _demo_limits(UserContext(user_id=None, is_demo=True), None, 3)
    assert settings.demo_center() == (-80.25, 43.55)
    assert box[0] < -80.25 < box[2] and box[1] < 43.55 < box[3]


def test_bad_demo_centre_setting_falls_back(monkeypatch):
    monkeypatch.setenv("FISHDEX_DEMO_CENTER", "not,a,point")
    assert settings.demo_center() == (-79.7, 43.45)
