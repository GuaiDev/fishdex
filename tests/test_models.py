"""Tests for Pydantic models."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.models.catch import Catch
from src.models.profile import UserProfile
from src.models.trip import Trip


def test_catch_with_no_measurements_validates():
    c = Catch(species="brook trout")
    assert c.species == "brook trout"
    assert c.length_cm is None
    assert c.weight_kg is None
    assert c.released is True


def test_trip_round_trips_through_json():
    original = Trip(
        date=date(2026, 5, 15),
        jurisdiction="CA-ON",
        location_name="Credit River",
        species_caught=[Catch(species="smallmouth bass", length_cm=35.5)],
        gear_used=["spinning rod"],
        what_worked="chartreuse jig",
    )
    decoded = Trip.model_validate_json(original.model_dump_json())
    assert decoded.location_name == "Credit River"
    assert decoded.jurisdiction == "CA-ON"
    assert len(decoded.species_caught) == 1
    assert decoded.species_caught[0].species == "smallmouth bass"
    assert decoded.species_caught[0].length_cm == 35.5


def test_trip_rejects_invalid_jurisdiction_code():
    with pytest.raises(ValidationError):
        Trip(date=date(2026, 5, 15), jurisdiction="ontario", location_name="X")


def test_trip_accepts_valid_jurisdictions():
    for code in ["CA-ON", "US-MI", "US-NY"]:
        t = Trip(date=date(2026, 5, 15), jurisdiction=code, location_name="X")
        assert t.jurisdiction == code


def test_user_profile_default_is_ontario():
    p = UserProfile.default()
    assert p.home_jurisdiction == "CA-ON"
    assert p.home_location is not None
    assert "Toronto" in p.home_location.name
    assert len(p.target_species) > 0


def test_user_profile_round_trip():
    p = UserProfile.default()
    decoded = UserProfile.model_validate_json(p.model_dump_json())
    assert decoded.home_jurisdiction == p.home_jurisdiction
    assert decoded.target_species == p.target_species
    assert decoded.home_location == p.home_location
