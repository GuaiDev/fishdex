"""Seed replacement must preserve personal rows and fail atomically."""

import sqlite3

import pytest
from typer.testing import CliRunner

from src.storage.ownership import PERSONAL_TABLES, TABLE_OWNERSHIP, import_shared


def _insert_required(db, table, **values):
    for col in db.execute(f'PRAGMA table_info("{table}")'):
        _, name, kind, required, default, pk = col
        if name not in values and required and default is None:
            values[name] = 1 if kind in ("INTEGER", "REAL", "FLOAT") else "test"
    db[table].insert(values)
    db.conn.commit()


def _shared_rows(db, label):
    _insert_required(db, "spots", spot_id=label, jurisdiction="CA-ON")
    _insert_required(db, "water_bodies", water_body_id=label, jurisdiction="CA-ON")
    db["species_ranges"].insert({"species": label, "jurisdictions_present": '["CA-ON"]'})
    db["stream_segments"].insert(
        {
            "ogf_id": 42,
            "jurisdiction": "CA-ON",
            "min_lon": -80,
            "max_lon": -79,
            "min_lat": 43,
            "max_lat": 44,
        }
    )
    db["reddit_posts"].insert({"post_id": label, "title": label, "body": "fish"})
    db.conn.commit()


def _personal_snapshot(db):
    return {
        table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        for table in PERSONAL_TABLES
        if table in db.table_names()
    }


def test_import_preserves_two_users_three_sessions_two_pins(tmp_path, db_factory):
    source = db_factory(tmp_path / "source.db")
    target = db_factory(tmp_path / "target.db")
    _shared_rows(source, "new")
    _shared_rows(target, "old")
    target["users"].insert({"id": 2, "username": "second"})
    for index in range(1, 4):
        target["sessions"].insert(
            {"id": index, "user_id": 1 if index == 1 else 2, "overall_notes": "Keep exactly: 🐟\n"}
        )
    for index in (1, 2):
        _insert_required(target, "pins", id=index, user_id=index, name="Private", lat=43, lng=-79)
    before = _personal_snapshot(target)
    source_before = list(source.conn.iterdump())
    counts = import_shared(tmp_path / "source.db", tmp_path / "target.db")
    assert target["users"].count == 2
    assert target["sessions"].count == 3
    assert target["pins"].count == 2
    assert _personal_snapshot(target) == before
    assert list(source.conn.iterdump()) == source_before
    assert counts["spots"] == (1, 1)
    assert target["spots"].get("new")["spot_id"] == "new"
    assert target["water_bodies"].get("new")["water_body_id"] == "new"
    assert target["species_ranges"].get("new")["species"] == "new"
    assert target.execute("SELECT id FROM stream_segments_rtree").fetchall() == [(42,)]
    assert target.execute(
        "SELECT rowid FROM reddit_posts_fts WHERE reddit_posts_fts MATCH 'new'"
    ).fetchall()


@pytest.mark.parametrize("table", ["users", "sessions", "pins", "unknown", "schema_migrations"])
def test_refuses_personal_and_unknown_tables(tmp_path, db_factory, table):
    db_factory(tmp_path / "source.db")
    db_factory(tmp_path / "target.db")
    with pytest.raises(ValueError, match="Refusing non-shared"):
        import_shared(tmp_path / "source.db", tmp_path / "target.db", tables=[table])


@pytest.mark.parametrize("failure", ["constraint", "personal_trigger", "schema"])
def test_failure_rolls_back_all_shared_tables(tmp_path, db_factory, failure):
    source = db_factory(tmp_path / "source.db")
    target = db_factory(tmp_path / "target.db")
    _shared_rows(source, "new")
    _shared_rows(target, "old")
    if failure == "constraint":
        target.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON spots BEGIN SELECT RAISE(ABORT, 'bad seed'); END"
        )
    elif failure == "personal_trigger":
        target.execute("CREATE TRIGGER fail AFTER DELETE ON spots BEGIN DELETE FROM users; END")
    else:
        source.execute("ALTER TABLE spots ADD COLUMN incompatible TEXT")
    source.conn.commit()
    target.conn.commit()
    before = list(target.conn.iterdump())
    with pytest.raises((sqlite3.Error, ValueError)):
        import_shared(tmp_path / "source.db", tmp_path / "target.db")
    assert list(target.conn.iterdump()) == before


def test_same_file_refused(tmp_path, db_factory):
    db_factory(tmp_path / "source.db")
    with pytest.raises(ValueError, match="different"):
        import_shared(tmp_path / "source.db", tmp_path / "source.db")


def test_ownership_covers_application_tables(db):
    assert set(db.table_names()) <= TABLE_OWNERSHIP.keys()


def test_cli_prints_counts_and_does_not_migrate(tmp_path, db_factory, monkeypatch):
    from src.cli.main import app
    from src.storage import database

    source = db_factory(tmp_path / "source.db")
    target = db_factory(tmp_path / "target.db")
    _shared_rows(source, "new")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "target.db")
    monkeypatch.setattr(database, "migrate", lambda *_: pytest.fail("Import must not migrate"))
    result = CliRunner().invoke(app, ["import-shared", str(tmp_path / "source.db")])
    assert result.exit_code == 0, result.output
    assert "spots: before=0 after=1" in result.output
    assert target["spots"].count == 1
