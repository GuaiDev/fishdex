"""delete-user and export-user: one angler's data goes or is exported, nobody else's."""

import json

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.storage.ownership import (
    EXPORT_SECTIONS,
    PERSONAL_TABLES,
    UNOWNED_PERSONAL_TABLES,
    count_user_rows,
    owner_predicates,
)

VICTIM, KEEPER = 2, 3


def _insert_required(db, table, _tag="x", **values):
    for _, name, kind, required, default, _pk in db.execute(f'PRAGMA table_info("{table}")'):
        if name not in values and required and default is None:
            values[name] = 1 if kind in ("INTEGER", "REAL", "FLOAT") else _tag
    db[table].insert(values)


def _populate(db, uid, photos_dir):
    """One row per owned personal table for this angler, plus a real photo file."""
    other = 90 + uid  # distinct ids so parent/child links stay unambiguous
    photo = photos_dir / f"{uid}.jpg"
    photo.write_bytes(b"jpeg")
    for table, where in owner_predicates(db).items():
        if table in ("users", "invite_codes", "recommendations", "personal_bests"):
            continue
        values = {}
        if "user_id" in where:
            values["user_id"] = uid
        if table == "sessions":
            values["id"] = other
        if table in ("stops", "session_conditions"):
            values["session_id"] = other
        if table == "catches":
            values.update(
                session_id=other,
                photo_path=str(photo),
                photo_url=f"/photos/{photo.name}",
                species="Creek Chub",
            )
        if table in ("chat_sessions",):
            values["session_id"] = f"s{uid}"
        if table == "chat_messages":
            values["session_id"] = f"s{uid}"
        if table in ("pin_links", "pin_recalled_visits"):
            values["pin_id"] = other
        if table == "pin_links":
            values.update(link_kind="confirmed", session_id=other)
        if table == "pins":
            values["id"] = other
        _insert_required(db, table, _tag=f"x{uid}", **values)
    trip_id = db.execute("SELECT MAX(id) FROM trips WHERE user_id = ?", [uid]).fetchone()[0]
    _insert_required(db, "recommendations", trip_id=trip_id)
    db["invite_codes"].insert({"code": f"c{uid}", "used_by": uid})
    db.conn.commit()


@pytest.fixture
def world(tmp_path, db_factory, monkeypatch):
    import src.cli.main as cli
    import src.services.photo_storage as photo_storage

    db = db_factory(tmp_path / "ops.db")
    photos = tmp_path / "photos"
    photos.mkdir()
    monkeypatch.setattr(photo_storage, "PHOTOS_DIR", photos)
    monkeypatch.setattr(cli, "get_db", lambda *a, **k: db)
    assert db.execute("SELECT role FROM users WHERE id = 1").fetchone()[0] == "admin"
    db["users"].insert({"id": VICTIM, "username": "Victim", "role": "beta"})
    db["users"].insert({"id": KEEPER, "username": "keeper", "role": "beta"})
    _populate(db, VICTIM, photos)
    _populate(db, KEEPER, photos)
    return db, photos


def _rows_for(db, uid):
    """Independent trace: every table with a user_id column, not just the ownership map."""
    found = {}
    for table in db.table_names():
        cols = {c.name for c in db[table].columns}
        if "user_id" in cols:
            n = db.execute(f'SELECT COUNT(*) FROM "{table}" WHERE user_id = ?', [uid]).fetchone()[0]
            if n:
                found[table] = n
    return found


def test_every_personal_table_is_attributable(world):
    db, _ = world
    assert set(owner_predicates(db)) == PERSONAL_TABLES - UNOWNED_PERSONAL_TABLES


def test_dry_run_changes_nothing(world):
    db, photos = world
    before = count_user_rows(db, VICTIM)
    result = CliRunner().invoke(app, ["delete-user", "victim", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would remove" in result.output
    assert count_user_rows(db, VICTIM) == before
    assert (photos / f"{VICTIM}.jpg").exists()


def test_delete_leaves_no_rows_or_photos_and_spares_others(world):
    db, photos = world
    keeper_before = count_user_rows(db, KEEPER)
    assert count_user_rows(db, VICTIM)["catches"] == 1
    result = CliRunner().invoke(app, ["delete-user", "victim"])
    assert result.exit_code == 0, result.output
    assert count_user_rows(db, VICTIM) == {}
    assert _rows_for(db, VICTIM) == {}
    assert db.execute("SELECT COUNT(*) FROM users WHERE id = ?", [VICTIM]).fetchone()[0] == 0
    assert (
        db.execute("SELECT COUNT(*) FROM invite_codes WHERE used_by = ?", [VICTIM]).fetchone()[0]
        == 0
    )
    assert [p.name for p in photos.iterdir()] == [f"{KEEPER}.jpg"]
    assert count_user_rows(db, KEEPER) == keeper_before


def test_delete_refuses_unknown_user(world):
    db, _ = world
    assert CliRunner().invoke(app, ["delete-user", "nobody"]).exit_code == 1
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3


def test_admin_account_can_be_previewed_and_deleted(world):
    db, _ = world
    runner = CliRunner()
    assert runner.invoke(app, ["delete-user", "admin", "--dry-run"]).exit_code == 0
    assert db.execute("SELECT COUNT(*) FROM users WHERE id = 1").fetchone()[0] == 1
    assert runner.invoke(app, ["delete-user", "admin"]).exit_code == 0
    assert db.execute("SELECT COUNT(*) FROM users WHERE id = 1").fetchone()[0] == 0


def test_export_tightens_permissions_on_existing_file(world, tmp_path):
    out = tmp_path / "existing.json"
    out.write_text("old")
    out.chmod(0o644)
    result = CliRunner().invoke(app, ["export-user", "victim", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.stat().st_mode & 0o777 == 0o600


def test_photo_path_outside_photos_dir_is_never_deleted(world, tmp_path):
    db, _ = world
    outside = tmp_path / "precious.txt"
    outside.write_text("keep")
    db["catches"].update(
        db.execute("SELECT id FROM catches WHERE user_id = ?", [VICTIM]).fetchone()[0],
        {"photo_path": str(outside), "photo_url": "/photos/../precious.txt"},
    )
    CliRunner().invoke(app, ["delete-user", "victim"])
    assert outside.exists()


def test_export_counts_match_database(world, tmp_path):
    db, _ = world
    out = tmp_path / "victim.json"
    result = CliRunner().invoke(app, ["export-user", "victim", "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert (out.stat().st_mode & 0o777) == 0o600
    assert data["user_id"] == VICTIM
    for section in ("profile", "sessions", "stops", "catches", "pins", "chat"):
        assert section in data
    for table, n in data["counts"].items():
        assert n == count_user_rows(db, VICTIM).get(table, 0)
        section = next(s for s, ts in EXPORT_SECTIONS.items() if table in ts)
        assert len(data[section][table]) == n
    assert data["counts"]["catches"] == 1 and data["counts"]["pins"] == 1
    assert data["profile"]["users"][0]["username"] == "Victim"
    assert all(r["user_id"] == VICTIM for r in data["sessions"]["sessions"])
    assert "token" not in json.dumps(data["profile"])
