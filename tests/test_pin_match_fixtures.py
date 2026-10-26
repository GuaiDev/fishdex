"""The recorded responses the UI is built against must be what the code returns.

tests/fixtures/pin_match_response.json and pin_today_response.json are the
output of the match and Today services on the world in tests/pin_world.py: a
temporary database, canned "right now" readings (10 °C, 14 km/h, 1011 hPa, and
a gauge reading 18.3 m³/s), and a fixed calendar day. They are not recordings
of live sources. Unlike the conditions fixture, nothing in them depends on the
network, which is why they can be checked exactly.

If the wording or shape changes on purpose, regenerate them:

    FISHDEX_WRITE_FIXTURES=1 uv run pytest tests/test_pin_match_fixtures.py
"""

import json
import os
from datetime import date
from pathlib import Path

import pytest

from src.models.pin import PinMatch, TodayResponse
from src.services.pins.match import pin_match
from src.services.pins.today import today_view
from src.storage.database import get_db
from tests.pin_world import SPOT, Angler, Canned, build_today_world, isolate

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 14)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    Canned().install(monkeypatch)
    alice = Angler(get_db(), 2, "alice")
    return alice, build_today_world(alice)


def _check(name: str, actual: dict) -> dict:
    path = FIXTURES / name
    if os.environ.get("FISHDEX_WRITE_FIXTURES"):
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    recorded = json.loads(path.read_text())
    assert actual == recorded, f"{name} is out of date: see this module's docstring"
    return recorded


def test_match_fixture_is_what_the_service_returns(world):
    alice, ids = world
    actual = pin_match(alice.db, alice.id, ids["bronte"])
    PinMatch.model_validate(_check("pin_match_response.json", actual))


def test_hidden_match_fixture_is_what_the_service_returns(world):
    alice, ids = world
    actual = pin_match(alice.db, alice.id, ids["humber"])
    recorded = _check("pin_match_hidden_response.json", actual)
    assert PinMatch.model_validate(recorded).status == "hidden"


def test_today_fixture_is_what_the_service_returns(world):
    alice, _ = world
    actual = today_view(alice.db, alice.id, "match", SPOT[0], SPOT[1], today=TODAY)
    TodayResponse.model_validate(_check("pin_today_response.json", actual))
