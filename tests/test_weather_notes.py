"""Weather-behaviour notes: facts go to everyone, the note only to someone still
new to the pressure concept, hedged, never a promise, and never a global level."""

import importlib
import json
import re
import shutil
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.agent import tools
from src.api.main import app
from src.auth.auth import generate_invite_code, redeem_invite_code
from src.services import presentation, terms, weather, weather_notes
from src.services.context import angler_profile, recompute_user_layer
from src.storage import angler_profile as store
from src.storage import database
from tests.test_angler_profile import trip

# Words that promise a result or state fish behaviour as fact.
PROMISE = re.compile(
    r"\b(will|guarantee|guaranteed|always|actively feeding|good time|expect|suppressed)\b",
    re.IGNORECASE,
)


def _conditions(trend):
    return SimpleNamespace(
        jurisdiction="CA-ON",
        time=datetime(2026, 10, 1, 12, 0),
        temperature_c=14.0,
        humidity_pct=70,
        precipitation_mm=0.0,
        wind_speed_kmh=10.0,
        pressure_hpa=1008.0,
        cloud_cover_pct=40,
        weather_code=2,
    )


@pytest.fixture
def fake_weather(monkeypatch):
    state = {"trend": "falling"}

    def trend_now(lat, lng):
        return SimpleNamespace(
            trend=state["trend"],
            current_hpa=1008.0,
            delta_24h_hpa=-4.0,
            delta_48h_hpa=-6.0,
            jurisdiction="CA-ON",
        )

    fake = SimpleNamespace(
        get_current_conditions=lambda lat, lng: _conditions(state["trend"]),
        compute_pressure_trend=trend_now,
    )
    monkeypatch.setattr(weather, "_weather", fake)
    return state


@pytest.fixture
def agent_db(db, monkeypatch):
    monkeypatch.setattr(tools, "get_db", lambda: db)
    return db


def _call(user_id=1):
    out = json.loads(tools.execute_tool("get_conditions", {"lat": 43.5, "lng": -79.8}, user_id))
    return out


def _make_familiar(db, user_id):
    store.write_inferred(db, user_id, {})
    for _ in range(weather_notes_threshold()):
        store.record_event(db, user_id, "barometric_pressure", "seen_on_map", "t")
    trip(db, user_id, "2025-06-01", 43.0, -80.0, [])
    recompute_user_layer(db, user_id=user_id)


def weather_notes_threshold():
    from src.services.context import profile as prof

    return prof.FAMILIAR_SEEN_COUNT


# ── the registry and the notes themselves ────────────────────────────────────


def test_pressure_is_a_registered_concept_with_no_map_layer():
    t = terms.TERMS["barometric_pressure"]
    assert t["mapped_from"] is None
    assert "fish" not in t["definition"].lower()


def test_notes_are_hedged_guidance_not_promises_or_unchecked_citations():
    for trend, note in weather_notes._PRESSURE_NOTES.items():
        assert not PROMISE.search(note.text), (trend, note.text)
        assert "rule of thumb" in note.text
        assert note.basis == "angler_rule_of_thumb"
        # No citable source was found; None, never an invented link.
        assert note.source is None
        assert note.term_id == "barometric_pressure"


def test_steady_pressure_has_no_note_and_the_old_notes_are_gone():
    assert weather_notes.note_for_trend("steady", "unseen") is None
    assert weather_notes.note_for_trend(None, "unseen") is None
    assert not hasattr(weather, "_FISHING_NOTES")


def test_a_familiar_concept_gets_no_note_new_and_introduced_do():
    assert weather_notes.note_for_trend("falling", "unseen")
    assert weather_notes.note_for_trend("rising", "introduced")
    assert weather_notes.note_for_trend("falling", "familiar") is None


def test_the_concept_does_not_move_map_layer_density(db):
    base = presentation.presentation(angler_profile(db, user_id=1))["map"]
    p = angler_profile(db, user_id=1)
    p.concepts["barometric_pressure"].state = "familiar"
    after = presentation.presentation(p)["map"]
    assert after["default_layer_density"] == base["default_layer_density"]


# ── the agent tool ───────────────────────────────────────────────────────────


def test_weather_facts_always_arrive_without_inline_notes(agent_db, fake_weather):
    out = _call()
    assert out["conditions"]["pressure_hpa"] == 1008.0
    assert out["conditions"]["pressure_trend"] == "falling"
    assert out["pressure"]["trend"] == "falling"
    assert "pressure_note" not in out["conditions"]
    assert "fishing_note" not in out["pressure"]


def test_a_new_user_gets_the_note_and_it_counts_as_a_sighting(agent_db, fake_weather):
    note = _call(1)["pressure_note"]
    assert note["term_id"] == "barometric_pressure"
    assert note["source"] is None
    assert store.event_counts(agent_db, 1)[("barometric_pressure", "seen_on_map")][0] == 1
    _call(1)
    assert store.event_counts(agent_db, 1)[("barometric_pressure", "seen_on_map")][0] == 2


def test_no_note_and_no_sighting_for_steady_pressure(agent_db, fake_weather):
    fake_weather["trend"] = "steady"
    assert _call(1)["pressure_note"] is None
    assert store.event_counts(agent_db, 1) == {}


def test_a_familiar_user_keeps_the_facts_and_loses_the_note(agent_db, fake_weather):
    _make_familiar(agent_db, 1)
    assert angler_profile(agent_db, user_id=1).concepts["barometric_pressure"].state == "familiar"
    out = _call(1)
    assert out["pressure_note"] is None
    assert out["conditions"]["pressure_trend"] == "falling"
    assert out["pressure"]["delta_24h_hpa"] == -4.0
    # Not shown, so not counted again.
    assert store.event_counts(agent_db, 1)[("barometric_pressure", "seen_on_map")][0] == (
        weather_notes_threshold()
    )


def test_literacy_is_per_user(agent_db, fake_weather):
    _make_familiar(agent_db, 1)
    assert _call(1)["pressure_note"] is None
    assert _call(2)["pressure_note"] is not None


def test_a_declared_familiar_value_is_honoured(agent_db, fake_weather):
    store.set_declared(agent_db, 1, "concept:barometric_pressure", 1.0, 3.0)
    assert _call(1)["pressure_note"] is None


def test_repeated_notes_introduce_the_concept_without_a_new_rule(agent_db, fake_weather):
    for _ in range(5):
        assert _call(1)["pressure_note"]
    trip(agent_db, 1, "2025-06-01", 43.0, -80.0, [])
    recompute_user_layer(agent_db, user_id=1)
    state = angler_profile(agent_db, user_id=1).concepts["barometric_pressure"].state
    assert state == "introduced"
    assert _call(1)["pressure_note"]  # introduced still gets it


def test_a_broken_profile_read_still_returns_the_weather(agent_db, fake_weather, monkeypatch):
    monkeypatch.setattr(
        "src.services.context.angler_profile", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    out = _call(1)
    assert out["conditions"]["temperature_c"] == 14.0
    assert out["pressure_note"] is not None  # falls back to unseen, not to silence


def test_the_tool_description_tells_the_model_the_note_is_conditional():
    spec = next(t for t in tools.tool_schemas(None) if t["name"] == "get_conditions")
    assert "pressure_note" in spec["description"]
    assert "rule of thumb" in spec["description"]


# ── /conditions ──────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, _template_db, fake_weather):
    shutil.copy(_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    handle = database.get_db()
    # The endpoint reads the ingest module directly, so patch it there.
    ingest = importlib.import_module("src.ingest.global.weather")
    monkeypatch.setattr(ingest, "get_current_conditions", weather._weather.get_current_conditions)
    monkeypatch.setattr(ingest, "compute_pressure_trend", weather._weather.compute_pressure_trend)
    got = redeem_invite_code(handle, generate_invite_code(handle), "angler")
    return SimpleNamespace(
        client=TestClient(app),
        db=handle,
        id=got["user_id"],
        h={"Authorization": f"Bearer {got['token']}"},
    )


def _panel(api):
    r = api.client.get("/conditions?lat=43.5&lng=-79.8", headers=api.h)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_panel_serves_the_hedged_note_with_its_term_id_to_a_new_user(api):
    body = _panel(api)
    assert "rule of thumb" in body["pressure_note"]
    assert body["pressure_note_term_id"] == "barometric_pressure"
    assert body["pressure_note_basis"] == "angler_rule_of_thumb"
    assert body["pressure_note_source"] is None
    assert body["pressure_trend"] == "falling"
    # A panel refresh is not proof it was read: no sighting recorded.
    assert store.event_counts(api.db, api.id) == {}


def test_the_panel_keeps_the_facts_but_drops_the_note_once_familiar(api):
    store.set_declared(api.db, api.id, "concept:barometric_pressure", 1.0, 3.0)
    body = _panel(api)
    assert body["pressure_note"] is None
    assert body["pressure_note_term_id"] is None
    assert body["pressure_hpa"] == 1008.0
    assert body["pressure_trend"] == "falling"
    assert body["pressure_delta_24h_hpa"] == -4.0
