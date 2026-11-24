"""Tests for system prompt assembly: every input is read for one user from the database."""

import pytest

from src.agent.system_prompt import assemble, prompt_inputs
from src.models.profile import Location
from src.storage.profile import set_user_home

TEMPLATE = "# Test template\n\nThis is the persona prompt."
GUELPH = Location(name="Guelph, ON", lat=43.55, lng=-80.25)
KAMLOOPS = Location(name="Kamloops, BC", lat=50.67, lng=-120.33)


def _user(db, user_id, username):
    db["users"].insert({"id": user_id, "username": username}, pk="id", replace=True)


def test_guelph_user_sees_guelph_and_ontario(db):
    _user(db, 5, "guelph_angler")
    set_user_home(db, 5, GUELPH, "CA-ON")

    out = assemble(TEMPLATE, db, 5)

    assert "## Your angler" in out
    assert "Guelph, ON" in out
    assert "Home jurisdiction: CA-ON" in out
    assert "## Active jurisdiction" in out
    assert "## Recent trips" not in out


def test_home_and_jurisdiction_follow_the_user(db):
    _user(db, 5, "guelph_angler")
    _user(db, 6, "bc_angler")
    set_user_home(db, 5, GUELPH, "CA-ON")
    set_user_home(db, 6, KAMLOOPS, "CA-BC")

    out = assemble(TEMPLATE, db, 6)

    assert "Kamloops, BC" in out and "Home jurisdiction: CA-BC" in out
    assert "Guelph" not in out


def test_user_with_no_home_gets_the_deploy_default(db, monkeypatch):
    _user(db, 7, "newcomer")
    monkeypatch.setenv("FISHDEX_DEFAULT_JURISDICTION", "CA-BC")

    inputs = prompt_inputs(db, 7)
    out = assemble(TEMPLATE, db, 7)

    assert inputs.home_location is None
    assert inputs.jurisdiction.code == "CA-BC"
    assert "- Home: (not set)" in out


def test_jurisdiction_without_coordinates_is_kept(db):
    _user(db, 8, "bc_angler")
    set_user_home(db, 8, None, "CA-BC")

    inputs = prompt_inputs(db, 8)

    assert inputs.home_location is None
    assert inputs.jurisdiction.code == "CA-BC"


def test_unknown_jurisdiction_carries_disclaimer(db):
    _user(db, 9, "florida_angler")
    set_user_home(db, 9, None, "US-FL")

    out = assemble(TEMPLATE, db, 9)

    assert "don't recognize" in out.lower()
    assert "US-FL" in out


def test_prompt_never_reads_the_profile_file_or_trips_table(db, monkeypatch):
    import src.storage.profile as profile_mod

    def boom(*args, **kwargs):
        raise AssertionError("legacy profile file read")

    monkeypatch.setattr(profile_mod, "load_profile", boom)
    _user(db, 5, "guelph_angler")
    set_user_home(db, 5, GUELPH, "CA-ON")

    assert "Guelph" in assemble(TEMPLATE, db, 5)


@pytest.mark.parametrize("user_id", [1, 2])
def test_any_user_id_is_treated_alike(db, user_id):
    _user(db, user_id, f"u{user_id}")
    set_user_home(db, user_id, GUELPH, "CA-ON")
    assert "Guelph, ON" in assemble(TEMPLATE, db, user_id)
