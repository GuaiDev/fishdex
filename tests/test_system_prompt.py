"""Tests for system prompt assembly."""

from datetime import date

from src.agent.system_prompt import assemble
from src.jurisdictions.registry import get_jurisdiction
from src.jurisdictions.unknown import UnknownJurisdiction
from src.models.catch import Catch
from src.models.profile import UserProfile
from src.models.trip import Trip

TEMPLATE = "# Test template\n\nThis is the persona prompt."


def test_assemble_with_no_trips():
    out = assemble(TEMPLATE, UserProfile.default(), [], get_jurisdiction("CA-ON"))
    assert "No trips logged yet" in out
    assert "## Your angler" in out
    assert "## Active jurisdiction" in out


def test_assemble_includes_profile_and_trip_data():
    p = UserProfile.default()
    p.target_species = ["smallmouth bass", "brook trout"]
    trips = [
        Trip(
            date=date(2026, 5, 1),
            jurisdiction="CA-ON",
            location_name="Lake Simcoe",
            species_caught=[Catch(species="northern pike", length_cm=60.0)],
            what_worked="topwater frog",
        ),
        Trip(
            date=date(2026, 5, 8),
            jurisdiction="CA-ON",
            location_name="Credit River",
        ),
    ]
    out = assemble(TEMPLATE, p, trips, get_jurisdiction("CA-ON"))
    assert "smallmouth bass" in out
    assert "Lake Simcoe" in out
    assert "Credit River" in out
    assert "MNRF" in out


def test_assemble_with_unknown_jurisdiction_carries_disclaimer():
    out = assemble(TEMPLATE, UserProfile.default(), [], UnknownJurisdiction(code="US-FL"))
    assert "don't recognize" in out.lower()
    assert "US-FL" in out
