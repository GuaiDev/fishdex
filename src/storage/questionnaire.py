"""Per-user storage for onboarding answers.

One row per (user, questionnaire version, question). Every query filters on user_id.
Answers are only ever written inside account creation (src/auth/auth.py), so a user
row without these answers means the account predates the questionnaire.
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlite_utils import Database

TABLE = "questionnaire_answers"


def ensure_tables(db: Database) -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id     INTEGER NOT NULL,
            version     TEXT NOT NULL,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            answered_at TEXT NOT NULL,
            PRIMARY KEY (user_id, version, key)
        )
    """)
    db.conn.commit()


def save_answers(db: Database, user_id: int, version: str, answers: dict[str, Any]) -> None:
    """Store one user's answers for a questionnaire version, replacing any earlier ones."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with db.conn:
        for key, value in answers.items():
            db.execute(
                f"INSERT OR REPLACE INTO {TABLE} (user_id, version, key, value, answered_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [user_id, version, key, json.dumps(value), now],
            )


def get_answers(db: Database, user_id: int, version: str | None = None) -> dict[str, Any]:
    """A user's answers keyed by question id; the newest version when none is named."""
    if version is None:
        row = db.execute(
            f"SELECT version FROM {TABLE} WHERE user_id = ? ORDER BY answered_at DESC, rowid DESC",
            [user_id],
        ).fetchone()
        if row is None:
            return {}
        version = row[0]
    rows = db.execute(
        f"SELECT key, value FROM {TABLE} WHERE user_id = ? AND version = ?", [user_id, version]
    ).fetchall()
    return {k: json.loads(v) for k, v in rows}
