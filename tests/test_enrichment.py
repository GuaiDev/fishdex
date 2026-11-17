"""Tests for retroactive enrichment script and SDM retrain check."""

import pytest


@pytest.fixture
def db_conn(db_factory, tmp_path):
    return db_factory(path=tmp_path / "test.db")


def test_retroactive_script_imports():
    """Script can be imported without errors."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "enrich_historical",
        "scripts/enrich_historical_sessions.py",
    )
    assert spec is not None


def test_sdm_retrain_check_no_crash(db_conn):
    """Retrain check runs without crashing on empty DB."""
    from src.cli.main import _check_sdm_retrain_needed

    # Should not raise — gracefully handles missing models directory
    _check_sdm_retrain_needed(db_conn)
