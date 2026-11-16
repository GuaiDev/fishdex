"""Each user's home and jurisdiction live in the database, not in the captain's profile file."""

import json

import pytest

import src.agent.chat as chat
import src.services.context.place as place_mod
import src.storage.profile as profile_mod
from src.agent.system_prompt import assemble
from src.models.profile import Location
from src.storage.database import MIGRATIONS, get_db, migrate
from src.storage.profile import get_user_home, set_user_home

OAKVILLE = Location(name="Oakville, ON", lat=43.4675, lng=-79.6877)
KAMLOOPS = Location(name="Kamloops, BC", lat=50.6745, lng=-120.3273)


@pytest.fixture(autouse=True)
def _isolated_profile_file(tmp_path, monkeypatch):
    """Nothing here may read or write the real data/user_profile.json."""
    monkeypatch.setattr(profile_mod, "PROFILE_PATH", tmp_path / "profile.json")


def _segment(db, ogf_id, name, lat, lng, jurisdiction):
    db["stream_segments"].insert(
        {
            "ogf_id": ogf_id,
            "name": name,
            "watercourse_type": "Stream",
            "geom_wkt": f"LINESTRING({lng - 0.001} {lat - 0.001}, {lng + 0.001} {lat + 0.001})",
            "stream_order": 3,
            "jurisdiction": jurisdiction,
        }
    )


def _user(db, user_id, username):
    db["users"].insert({"id": user_id, "username": username}, pk="id", replace=True)


@pytest.fixture
def two_mill_creeks(db):
    # The Ontario one is listed first so an unranked lookup would pick it.
    _segment(db, 1, "Mill Creek", 43.50, -79.70, "CA-ON")
    _segment(db, 2, "Mill Creek", 50.70, -120.30, "CA-BC")
    _user(db, 1, "jason")
    _user(db, 2, "bc_angler")
    set_user_home(db, 1, OAKVILLE, "CA-ON")
    set_user_home(db, 2, KAMLOOPS, "CA-BC")
    return db


def test_bc_user_mill_creek_resolves_nearest_their_own_home(two_mill_creeks):
    p = place_mod.resolve(two_mill_creeks, query="Mill Creek", user_id=2)
    assert p is not None
    assert p.lat == pytest.approx(50.70, abs=0.01)
    assert p.lng == pytest.approx(-120.30, abs=0.01)


def test_ontario_user_still_gets_the_ontario_mill_creek(two_mill_creeks):
    p = place_mod.resolve(two_mill_creeks, query="Mill Creek", user_id=1)
    assert p.lat == pytest.approx(43.50, abs=0.01)


def test_a_user_with_no_home_is_not_ranked_by_the_captains(db):
    _segment(db, 1, "Mill Creek", 43.50, -79.70, "CA-ON")
    _user(db, 1, "jason")
    _user(db, 3, "newcomer")
    set_user_home(db, 1, OAKVILLE, "CA-ON")

    assert place_mod._home_point(db, 3) is None
    assert place_mod._home_point(db, 1) == (OAKVILLE.lat, OAKVILLE.lng)


def test_home_round_trips_with_its_jurisdiction(two_mill_creeks):
    home, jurisdiction = get_user_home(two_mill_creeks, 2)
    assert home == KAMLOOPS
    assert jurisdiction == "CA-BC"
    assert get_user_home(two_mill_creeks, 99) is None


def test_bc_users_prompt_carries_their_home_and_jurisdiction(two_mill_creeks):
    profile, trips, jurisdiction = chat._prompt_inputs(two_mill_creeks, 2)
    prompt = assemble("template", profile, trips, jurisdiction)

    assert "Kamloops, BC (CA-BC)" in prompt
    assert jurisdiction.code == "CA-BC"
    assert "Oakville" not in prompt
    assert trips == []


def test_user_with_no_home_gets_no_profile(db):
    _user(db, 3, "newcomer")
    profile, trips, jurisdiction = chat._prompt_inputs(db, 3)
    assert profile is None
    assert trips == []
    assert jurisdiction.code == chat._DEFAULT_JURISDICTION


def test_stored_home_overrides_the_profile_file_for_user_1(two_mill_creeks, tmp_path):
    profile_mod.save_profile(profile_mod.UserProfile.default(), path=profile_mod.PROFILE_PATH)
    set_user_home(two_mill_creeks, 1, KAMLOOPS, "CA-BC")

    profile, _, jurisdiction = chat._prompt_inputs(two_mill_creeks, 1)

    assert profile.home_location == KAMLOOPS
    assert jurisdiction.code == "CA-BC"
    # the rest of the profile still comes from the file
    assert "brook trout" in profile.target_species


def _migrated_up_to_previous(path):
    """A database as it was before this migration: every earlier step run."""
    db = get_db(path)
    last = MIGRATIONS[-1]
    assert last.name == "user_home"
    for m in MIGRATIONS[:-1]:
        m.apply(db)
    return db


@pytest.mark.raw_db
def test_migration_seeds_user_1_from_the_profile_file(tmp_path):
    profile_mod.PROFILE_PATH.write_text(
        json.dumps(
            {
                "home_jurisdiction": "CA-ON",
                "home_location": {"name": "Guelph, ON", "lat": 43.55, "lng": -80.25},
            }
        )
    )
    db = _migrated_up_to_previous(tmp_path / "t.db")

    migrate(db)
    migrate(db)  # idempotent

    home, jurisdiction = get_user_home(db, 1)
    assert (home.name, home.lat, home.lng) == ("Guelph, ON", 43.55, -80.25)
    assert jurisdiction == "CA-ON"


@pytest.mark.raw_db
def test_migration_without_a_profile_file_invents_no_home(tmp_path):
    db = _migrated_up_to_previous(tmp_path / "t.db")
    migrate(db)
    assert get_user_home(db, 1) is None
    assert not profile_mod.PROFILE_PATH.exists()


@pytest.mark.raw_db
def test_migration_never_overwrites_a_stored_home(tmp_path):
    db = _migrated_up_to_previous(tmp_path / "t.db")
    migrate(db)
    set_user_home(db, 1, KAMLOOPS, "CA-BC")
    profile_mod.PROFILE_PATH.write_text(profile_mod.UserProfile.default().model_dump_json())

    db.execute("DELETE FROM schema_migrations WHERE name = 'user_home'")
    migrate(db)

    assert get_user_home(db, 1)[0] == KAMLOOPS


@pytest.mark.raw_db
def test_migration_preserves_jurisdiction_without_a_home(tmp_path):
    profile_mod.PROFILE_PATH.write_text(
        json.dumps({"home_jurisdiction": "CA-BC", "home_location": None})
    )
    db = _migrated_up_to_previous(tmp_path / "t.db")
    migrate(db)
    profile_mod.PROFILE_PATH.unlink()

    assert get_user_home(db, 1) == (None, "CA-BC")
    profile, _, jurisdiction = chat._prompt_inputs(db, 1)
    assert profile.home_location is None
    assert profile.home_jurisdiction == "CA-BC"
    assert jurisdiction.code == "CA-BC"
    assert place_mod._home_point(db, 1) is None


def test_jurisdiction_without_a_home_is_per_user(db):
    _user(db, 2, "bc_angler")
    db.execute("UPDATE users SET home_jurisdiction = ? WHERE id = ?", ["CA-BC", 2])

    profile, trips, jurisdiction = chat._prompt_inputs(db, 2)

    assert profile.home_location is None
    assert profile.home_jurisdiction == "CA-BC"
    assert jurisdiction.code == "CA-BC"
    assert trips == []
    assert place_mod._home_point(db, 2) is None


@pytest.mark.parametrize("initial_home", [None, OAKVILLE])
def test_profile_edit_persists_jurisdiction_without_coordinates(db, monkeypatch, initial_home):
    import src.cli.main as cli

    _user(db, 1, "jason")
    set_user_home(db, 1, initial_home, "CA-ON")
    monkeypatch.setattr(cli, "get_db", lambda: db)
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: True)
    answers = iter(["CA-BC", "", "", "", "trout", "fly fishing", "exploration"])
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: next(answers))

    cli.profile()

    assert get_user_home(db, 1) == (None, "CA-BC")
    profile, _, jurisdiction = chat._prompt_inputs(db, 1)
    assert profile.home_location is None
    assert profile.home_jurisdiction == "CA-BC"
    assert jurisdiction.code == "CA-BC"
