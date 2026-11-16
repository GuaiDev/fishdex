"""Durable ingest ledger behavior."""

import logging

from src.services.admin_dashboard import build_dashboard
from src.services.ingest_runs import ingest_run
from src.storage.database import get_db


def test_silent_empty_run_is_flagged_and_visible_in_admin_dashboard(tmp_path, caplog):
    db = get_db(tmp_path / "test.db")
    with caplog.at_level(logging.WARNING, logger="src.services.ingest_runs"):
        with ingest_run("Overpass", "CA-ON", "Oakville", db=db) as run:
            # A successful HTTP response that yielded no records across a batch.
            run.record(fetched=20, stored=0, skipped=0, empty=18)

    row = db.execute(
        "SELECT fetched, stored, skipped, empty_share, status FROM ingest_runs"
    ).fetchone()
    assert row == (20, 0, 0, 0.9, "warning")
    assert "returned empty answers for 18/20" in caplog.text
    dashboard = build_dashboard(db)
    assert dashboard["ingest_status"]["runs"][0]["source"] == "Overpass"
    assert dashboard["ingest_status"]["runs"][0]["empty_share"] == 0.9


def test_ingest_run_records_failure_and_error(tmp_path):
    db = get_db(tmp_path / "test.db")
    try:
        with ingest_run("test source", db=db) as run:
            run.record(fetched=2, skipped=2)
            raise RuntimeError("upstream unavailable")
    except RuntimeError:
        pass

    row = db.execute("SELECT status, error, stored, skipped FROM ingest_runs").fetchone()
    assert row == ("failed", "RuntimeError: upstream unavailable", 0, 2)
