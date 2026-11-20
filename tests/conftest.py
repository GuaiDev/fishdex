"""Fixtures shared across tests: fast throwaway databases, per test."""

import shutil
import sqlite3

import pytest

import src.storage.profile as profile_mod
from src.storage.database import get_db, migrate


@pytest.fixture(scope="session")
def migrated_template_db(tmp_path_factory):
    """Build the schema once per pytest process (once per worker under xdist)."""
    path = tmp_path_factory.mktemp("template-session") / "template.db"
    template = get_db(path)
    # The user_home migration seeds user 1 from the profile file; the template must
    # not inherit whatever data/user_profile.json the machine running tests has.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(profile_mod, "PROFILE_PATH", path.parent / "no_profile.json")
        migrate(template)
    template.conn.close()
    _auto_migrate["template"] = path
    return path


@pytest.fixture(scope="session")
def _template_db(migrated_template_db):
    return migrated_template_db


@pytest.fixture
def db_factory(migrated_template_db):
    def copy_database(path):
        if not path.exists():
            shutil.copy(migrated_template_db, path)
        return get_db(path)

    return copy_database


@pytest.fixture
def db(tmp_path, db_factory):
    return db_factory(tmp_path / "spots.db")


@pytest.fixture
def fast_db(tmp_path, db_factory):
    return db_factory(tmp_path / "test.db")


@pytest.fixture
def curation(tmp_path):
    path = tmp_path / "curation.toml"
    path.write_text(
        'version = 1\njurisdiction = "CA-ON"\nbuild_bbox = [-80.5, 42.9, -79.0, 44.0]\n'
    )
    return path


@pytest.fixture(autouse=True)
def _no_live_ohn(monkeypatch):
    """`build_spots` reaches the OHN, LIO and OSM services by default; no test is allowed to.

    All entry points are stubbed at the module where `spots` resolves them at call
    time, so a build in a test resolves no outlines, reads no watercourses, finds
    no bathymetry and fetches no shore structures. A test that wants outlines or a
    survey injects its own resolvers through `build_spots` instead.
    """
    import importlib

    from src.ingest.jurisdictions.ca_on import hydro_network
    from src.services import bathymetry, lake_outlines

    osm = importlib.import_module("src.ingest.global.osm")
    monkeypatch.setattr(lake_outlines, "resolve_lake_outlines", lambda mouths, **k: {})
    monkeypatch.setattr(hydro_network, "fetch_watercourses_bbox", lambda *_a: [])
    monkeypatch.setattr(osm, "fetch_shore_structures", lambda *_a, **_k: ([], 0))
    monkeypatch.setattr(
        bathymetry, "fetch_lake_bathymetry", lambda *_a, **_k: bathymetry.LakeBathymetry(None, [])
    )


@pytest.fixture(autouse=True)
def _no_untapped_cache(monkeypatch, tmp_path):
    """`build_spots` wraps explore() as quiet-water spots, which reads the repo's score cache.

    Point it at a file that does not exist, so a build in a test finds no cache
    (the honest-empty path) and never depends on, or waits for, the real parquet.
    A test that wants quiet water writes its own parquet and points this at it.
    """
    from src.services import untapped_potential

    monkeypatch.setattr(untapped_potential, "_PARQUET_PATH", tmp_path / "no_untapped.parquet")


# Ordinary tests copy the migrated template instead of running migrations on
# every open. The import-time connection hook also covers module-scoped fixtures
# and seeds empty databases opened directly after the template is ready.
# `raw_db` restores production connection settings and leaves schema setup to
# the test, so migration tests can exercise the real opening behaviour.
_auto_migrate = {"on": True, "template": None}


def _install_auto_migrate() -> None:
    import src.storage.database as database

    real = database._configure_connection

    def _fast_test_pragmas(db):
        conn = db.conn
        # Test DBs are disposable; avoid fsync and WAL files for speed.
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = MEMORY")
        except Exception:
            pass
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA temp_store = MEMORY")

    def configure_then_migrate(db):
        if _auto_migrate["on"]:
            _fast_test_pragmas(db)
        else:
            real(db)
        if _auto_migrate["on"]:
            template = _auto_migrate["template"]
            if template is not None and not db.table_names():
                source = sqlite3.connect(template)
                try:
                    source.backup(db.conn)
                finally:
                    source.close()

    database._configure_connection = configure_then_migrate


_install_auto_migrate()


@pytest.fixture(autouse=True)
def _raw_db_opt_out(request, migrated_template_db):
    if request.node.get_closest_marker("raw_db"):
        _auto_migrate["on"] = False
        yield
        _auto_migrate["on"] = True
    else:
        yield


@pytest.fixture(autouse=True)
def _llm_ledger_in_tmp(monkeypatch, tmp_path, db_factory):
    """The gateway writes an `llm_usage` row per call; it must never reach the real database."""
    from src.llm import ledger

    path = tmp_path / "llm_ledger.db"

    def _open():
        db = db_factory(path)
        ledger.create_table(db)
        return db

    monkeypatch.setattr(ledger, "_open_db", _open)
