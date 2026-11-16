"""Fixtures shared across tests: fast throwaway databases, per test."""

import shutil

import pytest

from src.storage.database import _configure_connection, get_db, migrate


def pytest_collection_modifyitems(config, items):
    """Exclude network-marked tests by default unless explicitly selected."""
    markexpr = getattr(config.option, "markexpr", "") or ""
    if "network" in markexpr:
        return
    skip_network = pytest.mark.skip(reason="network tests excluded by default (use -m network)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)


@pytest.fixture(scope="session")
def migrated_template_db(tmp_path_factory):
    """Build the schema once per session and copy the file per test."""
    path = tmp_path_factory.mktemp("template-session") / "template.db"
    migrate(get_db(path))
    return path


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    """Schema setup costs over a second; build it once and copy the file per test."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    migrate(get_db(path))
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    path = tmp_path / "spots.db"
    shutil.copy(_template_db, path)
    return get_db(path)


@pytest.fixture
def fast_db(tmp_path, migrated_template_db):
    """Fresh migrated database copied from the session template."""
    path = tmp_path / "test.db"
    shutil.copy(migrated_template_db, path)
    return get_db(path)


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

    Imports are best-effort so tests that do not exercise these paths can still run
    in environments where optional geo deps are unavailable.
    """
    import importlib

    try:
        from src.ingest.jurisdictions.ca_on import hydro_network
    except Exception:
        hydro_network = None
    try:
        from src.services import bathymetry, lake_outlines
    except Exception:
        bathymetry = lake_outlines = None
    try:
        osm = importlib.import_module("src.ingest.global.osm")
    except Exception:
        osm = None

    if lake_outlines is not None:
        monkeypatch.setattr(lake_outlines, "resolve_lake_outlines", lambda mouths, **k: {})
    if hydro_network is not None:
        monkeypatch.setattr(hydro_network, "fetch_watercourses_bbox", lambda *_a: [])
    if osm is not None:
        monkeypatch.setattr(osm, "fetch_shore_structures", lambda *_a, **_k: ([], 0))
    if bathymetry is not None:
        from types import SimpleNamespace
        monkeypatch.setattr(
            bathymetry, "fetch_lake_bathymetry",
            lambda *_a, **_k: SimpleNamespace(contours=None, shoals=[], max_depth_m=None)
            if not hasattr(bathymetry, "LakeBathymetry") else bathymetry.LakeBathymetry(None, []),
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


# `get_db()` only opens the file now; the schema is built by `migrate`, once, at
# deploy. Tests open a fresh temporary database at nearly every turn and expect
# the schema to be there, so migrate right after each open, and only when the
# database is behind (a read when it is not). The patch sits at import time so
# module-scoped fixtures get it too. A test that needs the bare production
# behaviour, `get_db()` and nothing else, opts out with `@pytest.mark.raw_db`.
_auto_migrate = {"on": True}


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
            database.migrate(db)

    database._configure_connection = configure_then_migrate


_install_auto_migrate()


@pytest.fixture(autouse=True)
def _raw_db_opt_out(request):
    if request.node.get_closest_marker("raw_db"):
        _auto_migrate["on"] = False
        yield
        _auto_migrate["on"] = True
    else:
        yield


@pytest.fixture(autouse=True)
def _llm_ledger_in_tmp(monkeypatch, tmp_path):
    """The gateway writes an `llm_usage` row per call; it must never reach the real database."""
    from src.llm import ledger
    from src.storage.database import get_db

    path = tmp_path / "llm_ledger.db"

    def _open():
        db = get_db(path)
        ledger.create_table(db)
        return db

    monkeypatch.setattr(ledger, "_open_db", _open)
