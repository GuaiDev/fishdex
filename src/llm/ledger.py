"""The `llm_usage` ledger: one row per model call."""

import logging
from dataclasses import dataclass

from sqlite_utils import Database

logger = logging.getLogger(__name__)

TABLE = "llm_usage"

# Rows that could not be written since the process started. A ledger that fails
# quietly is indistinguishable from an agent that never calls the model, so the
# count is kept where a test or a health check can read it.
write_failures = 0


@dataclass(frozen=True)
class UsageRow:
    user_id: int | None
    caller: str
    role: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0
    est_usd: float | None = None
    latency_ms: int = 0
    session_id: str | None = None
    status: str = "ok"
    error: str | None = None


def create_table(db: Database) -> None:
    """Migration 28. Idempotent."""
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                    TEXT NOT NULL DEFAULT (datetime('now')),
            user_id               INTEGER,
            session_id            TEXT,
            caller                TEXT NOT NULL,
            role                  TEXT NOT NULL,
            model                 TEXT NOT NULL,
            input_tokens          INTEGER NOT NULL DEFAULT 0,
            output_tokens         INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
            web_searches          INTEGER NOT NULL DEFAULT 0,
            est_usd               REAL,
            latency_ms            INTEGER NOT NULL DEFAULT 0,
            status                TEXT NOT NULL DEFAULT 'ok',
            error                 TEXT
        )
    """)
    db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_ts ON {TABLE}(ts)")
    db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_user ON {TABLE}(user_id, ts)")
    db.conn.commit()


def _open_db() -> Database:
    from src.storage.database import get_db

    return get_db()


def record(row: UsageRow) -> bool:
    """Write one row. Never raises: a ledger fault must not fail the user's request.

    Returns whether the row was written, and logs at WARNING when it was not.
    """
    global write_failures
    try:
        db = _open_db()
        try:
            db[TABLE].insert(
                {
                    "user_id": row.user_id,
                    "session_id": row.session_id,
                    "caller": row.caller,
                    "role": row.role,
                    "model": row.model,
                    "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens,
                    "cache_creation_tokens": row.cache_creation_tokens,
                    "cache_read_tokens": row.cache_read_tokens,
                    "web_searches": row.web_searches,
                    "est_usd": row.est_usd,
                    "latency_ms": row.latency_ms,
                    "status": row.status,
                    "error": row.error,
                }
            )
        finally:
            db.conn.close()
        return True
    except Exception:  # noqa: BLE001
        write_failures += 1
        logger.warning(
            "llm_usage row not written (caller=%s role=%s user=%s)",
            row.caller,
            row.role,
            row.user_id,
            exc_info=True,
        )
        return False
