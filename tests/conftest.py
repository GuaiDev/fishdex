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
