"""Tests for profile load/save."""

from src.models.profile import UserProfile
from src.storage.profile import load_profile, save_profile


def test_load_on_missing_file_creates_default(tmp_path):
    profile_path = tmp_path / "profile.json"
    p = load_profile(path=profile_path)
    assert p.home_jurisdiction == "CA-ON"
    assert profile_path.exists()


def test_save_and_load_round_trip(tmp_path):
    profile_path = tmp_path / "profile.json"
    original = UserProfile.default()
    original.target_species = ["brown trout", "muskellunge"]
    original.fishing_style = "stillwater fly fishing"
    save_profile(original, path=profile_path)

    loaded = load_profile(path=profile_path)
    assert loaded.target_species == ["brown trout", "muskellunge"]
    assert loaded.fishing_style == "stillwater fly fishing"


def test_default_profile_anchored_to_oakville():
    p = UserProfile.default()
    assert p.home_location is not None
    assert "Oakville" in p.home_location.name


def test_an_old_profile_file_with_a_skill_level_still_loads_and_drops_it(tmp_path):
    import json

    profile_path = tmp_path / "profile.json"
    old = UserProfile.default().model_dump(mode="json")
    old["skill_level"] = "intermediate"
    profile_path.write_text(json.dumps(old))

    loaded = load_profile(path=profile_path)
    assert loaded.home_jurisdiction == "CA-ON"
    assert "skill_level" not in loaded.model_dump()
