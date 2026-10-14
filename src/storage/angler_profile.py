"""Per-user storage for the angler profile.

Two tables, both keyed by user_id, and every query here filters on it — this
project has had a cross-user leak before, and the tests check each function
against a second user.

  * `angler_profile_values`: one row per (user, key, source). `inferred` rows
    are rewritten by the recompute on every logged session; `declared` rows
    come from a future questionnaire (a starting value that fades) or from a
    settings override (`is_override`, which does not fade).
  * `angler_concept_events`: literacy evidence that happens between logged
    sessions — a definition opened, a feature seen on the map. Appended as it
    happens and folded into the concept states at the next recompute, so the
    profile still only changes once per logged session.

Writable keys are fixed (`models/angler_profile.SCALAR_KEYS` and
`concept:<term_id>` for a registered term). Anything else is refused, so no
skill or expertise field can come back through a questionnaire.
"""

import math
from datetime import UTC, datetime

from sqlite_utils import Database

from src.models.angler_profile import CONCEPT_KEY_PREFIX, SCALAR_KEYS, ProfileValue
from src.services import terms

VALUES = "angler_profile_values"
EVENTS = "angler_concept_events"

MAX_DECLARED_WEIGHT = 50.0

EVENT_KINDS: tuple[str, ...] = ("definition_opened", "seen_on_map")


def ensure_tables(db: Database) -> None:
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {VALUES} (
            user_id     INTEGER NOT NULL,
            key         TEXT NOT NULL,
            source      TEXT NOT NULL CHECK (source IN ('inferred', 'declared')),
            value       REAL NOT NULL CHECK (value BETWEEN 0 AND 1),
            weight      REAL NOT NULL CHECK (weight >= 0),
            is_override INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, key, source)
        )
    """)
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {EVENTS} (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            term_id     TEXT NOT NULL,
            kind        TEXT NOT NULL,
            ref         TEXT,
            at          TEXT NOT NULL
        )
    """)
    db.execute(f"CREATE INDEX IF NOT EXISTS idx_{EVENTS}_user ON {EVENTS}(user_id, term_id)")


def validate_key(key: str) -> str:
    """The key itself if it may hold a profile value; ValueError otherwise."""
    if key in SCALAR_KEYS:
        return key
    if key.startswith(CONCEPT_KEY_PREFIX) and key[len(CONCEPT_KEY_PREFIX) :] in terms.TERMS:
        return key
    raise ValueError(f"not a profile key: {key!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def set_declared(
    db: Database,
    user_id: int,
    key: str,
    value: float,
    weight: float,
    is_override: bool = False,
) -> ProfileValue:
    validate_key(key)
    if not math.isfinite(weight) or not 0.0 <= weight <= MAX_DECLARED_WEIGHT:
        raise ValueError(f"weight must be between 0 and {MAX_DECLARED_WEIGHT:g}")
    row = ProfileValue(
        value=value, source="declared", weight=weight, is_override=is_override, updated_at=_now()
    )
    ensure_tables(db)
    db.execute(
        f"INSERT INTO {VALUES} (user_id, key, source, value, weight, is_override, updated_at) "
        "VALUES (?, ?, 'declared', ?, ?, ?, ?) "
        "ON CONFLICT (user_id, key, source) DO UPDATE SET value = excluded.value, "
        "weight = excluded.weight, is_override = excluded.is_override, "
        "updated_at = excluded.updated_at",
        [user_id, key, row.value, row.weight, int(is_override), row.updated_at],
    )
    db.conn.commit()
    return row


def clear_declared(db: Database, user_id: int, key: str) -> bool:
    """Remove a declared value. Returns whether there was one."""
    validate_key(key)
    if VALUES not in db.table_names():
        return False
    cur = db.execute(
        f"DELETE FROM {VALUES} WHERE user_id = ? AND key = ? AND source = 'declared'",
        [user_id, key],
    )
    db.conn.commit()
    return cur.rowcount > 0


def write_inferred(db: Database, user_id: int, values: dict[str, tuple[float, float]]) -> None:
    """Replace this user's inferred rows with `{key: (value, weight)}`."""
    ensure_tables(db)
    now = _now()
    with db.conn:
        db.execute(f"DELETE FROM {VALUES} WHERE user_id = ? AND source = 'inferred'", [user_id])
        for key, (value, weight) in values.items():
            validate_key(key)
            db.execute(
                f"INSERT INTO {VALUES} (user_id, key, source, value, weight, is_override, "
                "updated_at) VALUES (?, ?, 'inferred', ?, ?, 0, ?)",
                [user_id, key, min(max(value, 0.0), 1.0), max(weight, 0.0), now],
            )


def get_values(db: Database, user_id: int) -> dict[str, dict[str, ProfileValue]]:
    """`{key: {source: ProfileValue}}` for one user."""
    if VALUES not in db.table_names():
        return {}
    out: dict[str, dict[str, ProfileValue]] = {}
    for key, source, value, weight, is_override, updated_at in db.execute(
        f"SELECT key, source, value, weight, is_override, updated_at FROM {VALUES} "
        "WHERE user_id = ?",
        [user_id],
    ).fetchall():
        out.setdefault(key, {})[source] = ProfileValue(
            value=value,
            source=source,
            weight=weight,
            is_override=bool(is_override),
            updated_at=updated_at,
        )
    return out


def record_event(
    db: Database, user_id: int, term_id: str, kind: str, ref: str | None = None
) -> int:
    if term_id not in terms.TERMS:
        raise ValueError(f"unknown term_id: {term_id!r}")
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown concept event kind: {kind!r}")
    ensure_tables(db)
    cur = db.execute(
        f"INSERT INTO {EVENTS} (user_id, term_id, kind, ref, at) VALUES (?, ?, ?, ?, ?)",
        [user_id, term_id, kind, ref, _now()],
    )
    db.conn.commit()
    return int(cur.lastrowid)


def event_counts(db: Database, user_id: int) -> dict[tuple[str, str], tuple[int, list[str]]]:
    """`{(term_id, kind): (count, a few refs)}` for one user."""
    if EVENTS not in db.table_names():
        return {}
    out: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for term_id, kind, n, refs in db.execute(
        f"SELECT term_id, kind, COUNT(*), GROUP_CONCAT(ref) FROM {EVENTS} "
        "WHERE user_id = ? GROUP BY term_id, kind",
        [user_id],
    ).fetchall():
        out[(term_id, kind)] = (int(n), [r for r in (refs or "").split(",") if r][:5])
    return out


def sessions_since(db: Database, user_id: int, since: str | None) -> int:
    """Sessions this user logged after a timestamp — how far a declared value has faded."""
    if since is None or "sessions" not in db.table_names():
        return 0
    row = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ? AND created_at > ?",
        [user_id, since.replace("T", " ")[:19]],
    ).fetchone()
    return int(row[0])
