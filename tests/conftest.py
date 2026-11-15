"""Fixtures shared by the spots and water-body tests: a throwaway database per test."""

import pytest


@pytest.fixture(scope="module")
def _template_db(tmp_path_factory):
    """Schema setup costs over a second; build it once and copy the file per test."""
    from src.storage.database import get_db

    path = tmp_path_factory.mktemp("template") / "template.db"
    get_db(path).conn.close()
    return path


@pytest.fixture
def db(tmp_path, _template_db):
    import shutil

    from src.storage.database import get_db

    path = tmp_path / "spots.db"
    shutil.copy(_template_db, path)
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

    def configure_then_migrate(db):
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
