"""Durable ingest run ledger and consistent empty-response diagnostics."""

import logging
from contextvars import ContextVar
from datetime import datetime

from sqlite_utils import Database

from src.storage.database import get_db

logger = logging.getLogger(__name__)

# Avoid alarming on small sources, while treating an empty majority as suspect.
EMPTY_SHARE_WARNING_FLOOR = 0.5
MIN_FETCHED_FOR_EMPTY_WARNING = 10


_active_run: ContextVar["IngestRun | None"] = ContextVar("active_ingest_run", default=None)


def record_counts(**counts: int) -> None:
    run = _active_run.get()
    if run is not None:
        run.record(**counts)


def record_error(exc: Exception) -> None:
    run = _active_run.get()
    if run is not None:
        run.error = f"{type(exc).__name__}: {exc}"


class IngestRun:
    """Mutable counts for one source invocation, persisted by the context manager."""

    def __init__(
        self,
        source: str,
        jurisdiction: str | None = None,
        area: str | None = None,
        db: Database | None = None,
    ):
        self.source = source
        self.jurisdiction = jurisdiction
        self.area = area
        self.fetched = 0
        self.stored = 0
        self.skipped = 0
        self.empty = 0
        self.error: str | None = None
        self.status = "running"
        self.run_id: int | None = None
        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._db = db

    @property
    def empty_share(self) -> float:
        return self.empty / self.fetched if self.fetched else 0.0

    def record(
        self, *, fetched: int = 0, stored: int = 0, skipped: int = 0, empty: int = 0
    ) -> None:
        """Add source counts. `fetched` is the number of answers examined."""
        self.fetched += fetched
        self.stored += stored
        self.skipped += skipped
        self.empty += empty

    def __enter__(self) -> "IngestRun":
        try:
            db = self._db or get_db()
            db.execute(
                "INSERT INTO ingest_runs (source, jurisdiction, area, started_at, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                [self.source, self.jurisdiction, self.area, self._started_at],
            )
            self.run_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.conn.commit()
        except Exception:
            self.run_id = None
            logger.exception("Could not start ingest ledger for %s", self.source)
        self._token = _active_run.set(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _active_run.reset(self._token)
        if exc is not None:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "failed"
        elif self.error:
            self.status = "failed"
        else:
            self.status = "success"

        if (
            self.fetched >= MIN_FETCHED_FOR_EMPTY_WARNING
            and self.empty_share >= EMPTY_SHARE_WARNING_FLOOR
        ):
            if self.status == "success":
                self.status = "warning"
            logger.warning(
                "ingest %s returned empty answers for %d/%d responses (%.0f%%)",
                self.source,
                self.empty,
                self.fetched,
                self.empty_share * 100,
            )
        if self.run_id is not None:
            try:
                db = self._db or get_db()
                db.execute(
                    "UPDATE ingest_runs SET finished_at=?, fetched=?, stored=?, skipped=?, "
                    "empty_count=?, empty_share=?, status=?, error=? WHERE id=?",
                    [
                        datetime.now().isoformat(timespec="seconds"),
                        self.fetched,
                        self.stored,
                        self.skipped,
                        self.empty,
                        self.empty_share,
                        self.status,
                        self.error,
                        self.run_id,
                    ],
                )
                db.conn.commit()
            except Exception:
                logger.exception("Could not finalize ingest ledger for %s", self.source)
        return False


def ingest_run(
    source: str,
    jurisdiction: str | None = None,
    area: str | None = None,
    db: Database | None = None,
) -> IngestRun:
    """Start a durable ledger row around an ingest operation."""
    return IngestRun(source, jurisdiction, area, db)
