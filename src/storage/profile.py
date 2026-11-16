"""User profile load/save (JSON-backed at data/user_profile.json)."""

from datetime import datetime
from pathlib import Path

from sqlite_utils import Database

from src.models.jurisdiction import JurisdictionCode
from src.models.profile import Location, UserProfile

PROFILE_PATH = Path("data/user_profile.json")


def load_profile(path: Path | None = None) -> UserProfile:
    p = path or PROFILE_PATH
    if not p.exists():
        profile = UserProfile.default()
        save_profile(profile, path=p)
        return profile
    return UserProfile.model_validate_json(p.read_text())


def save_profile(profile: UserProfile, path: Path | None = None) -> None:
    p = path or PROFILE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    profile = profile.model_copy(update={"updated_at": datetime.now()})
    p.write_text(profile.model_dump_json(indent=2))


def read_profile_file(path: Path) -> UserProfile | None:
    """The profile file as written, or None when it is absent or unreadable.

    Unlike `load_profile` this never creates the file and never falls back to the
    Oakville default, so a caller can tell "nobody chose a home" from "the default".
    """
    try:
        return UserProfile.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


def get_user_home(db: Database, user_id: int) -> tuple[Location, JurisdictionCode | None] | None:
    """The home a user stored in the database, with its jurisdiction, or None if unset."""
    if "users" not in db.table_names():
        return None
    row = db.execute(
        "SELECT home_name, home_lat, home_lng, home_jurisdiction FROM users WHERE id = ?",
        [user_id],
    ).fetchone()
    if row is None or row[1] is None or row[2] is None:
        return None
    return Location(name=row[0] or "", lat=float(row[1]), lng=float(row[2])), row[3]


def set_user_home(
    db: Database, user_id: int, home: Location, jurisdiction: JurisdictionCode
) -> None:
    """Store a user's home point and jurisdiction."""
    db.execute(
        "UPDATE users SET home_name = ?, home_lat = ?, home_lng = ?, home_jurisdiction = ? "
        "WHERE id = ?",
        [home.name, home.lat, home.lng, jurisdiction, user_id],
    )
    db.conn.commit()
