"""Persistence for the derived user layer.

The layer is recomputed when a session is logged, not when a question is
asked. Before this, every coaching question and every `get_my_fishing_summary`
re-read every stop the angler had ever logged and re-derived the same patterns
from scratch — work whose inputs change a few times a season, repeated several
times per conversation.

Two things make the cache safe to trust:

  * It stores a **fingerprint** of the inputs, not just a timestamp. A stale
    row is detected by the data having changed, not by having aged, so a
    direct DB edit or a restored backup cannot serve a wrong answer
    indefinitely.
  * A miss is not an error. `user_layer()` recomputes and stores. The cache is
    an optimisation, never a source of truth, and deleting the table changes
    performance rather than answers.
"""

import json
import logging

from sqlite_utils import Database

from src.models.angler_profile import SIGNALS_VERSION
from src.models.context import UserLayer

logger = logging.getLogger(__name__)

TABLE = "user_patterns"


def ensure_table(db: Database) -> None:
    if TABLE in db.table_names():
        return
    db[TABLE].create(
        {
            "user_id": int,
            "fingerprint": str,
            "layer_json": str,
            "computed_at": str,
        },
        pk="user_id",
    )


_FINGERPRINT_SOURCES = (
    ("stops", "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM stops WHERE user_id = ?"),
    ("sessions", "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM sessions WHERE user_id = ?"),
    (
        "insights",
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM behavioral_insights "
        "WHERE user_id = ? AND is_current = 1",
    ),
    (
        "catches",
        "SELECT COUNT(*), COALESCE(SUM(species_confirmed), 0) FROM catches WHERE user_id = ?",
    ),
    ("pins", "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM pins WHERE user_id = ?"),
)


# A layer stored before the signals existed, or under another signals version,
# is a miss: the profile it holds would answer with no signals at all.
_SIGNALS_PART = f"|signals:{SIGNALS_VERSION}"


def input_fingerprint(db: Database, user_id: int) -> str:
    """A cheap summary of everything `build_user_layer` reads.

    Literacy events (a definition opened, a feature seen) are deliberately not
    in it: they are folded in at the next recompute, so the profile changes
    once per logged session, not on every tap. Declared values are not in it
    either; they are blended at read time and need no recompute.

    Counts plus the newest row id. Any insert, delete or session-date change
    moves at least one of them. It will not notice an in-place edit of an
    existing stop's technique field — that is a deliberate trade: the check
    runs on every call and has to stay a few microseconds, and the write path
    recomputes explicitly anyway.

    One round trip, deliberately. The first version issued six — three
    `table_names()` calls plus a count each — which cost more than the
    derivation it was meant to avoid for any log under a few hundred stops. A
    cache whose freshness check is slower than a miss is not a cache.
    """
    # SQLite scalar subqueries return one column each, so the count and the
    # max id are asked for separately rather than as a tuple subquery.
    parts = []
    try:
        row = db.execute(
            "SELECT "
            + ", ".join(
                f"(SELECT COUNT(*) FROM {t} WHERE {w}), "
                f"(SELECT COALESCE({second}, 0) FROM {t} WHERE {w})"
                for t, w, second in (
                    ("stops", "user_id = ?", "MAX(id)"),
                    ("sessions", "user_id = ?", "MAX(id)"),
                    ("behavioral_insights", "user_id = ? AND is_current = 1", "MAX(id)"),
                    # Confirming a catch's species changes no count or id, but
                    # it is what the profile reads, so the confirmed total is
                    # part of the fingerprint.
                    ("catches", "user_id = ?", "SUM(species_confirmed)"),
                    ("pins", "user_id = ?", "MAX(id)"),
                )
            ),
            [user_id] * 10,
        ).fetchone()
    except Exception:  # noqa: BLE001 - a table missing on a fresh DB is normal
        return _fingerprint_slow(db, user_id)

    for i, name in enumerate(("stops", "sessions", "insights", "catches", "pins")):
        parts.append(f"{name}:{row[i * 2]}:{row[i * 2 + 1]}")
    return "|".join(parts) + _SIGNALS_PART


def _fingerprint_slow(db: Database, user_id: int) -> str:
    """Per-table fallback for a database missing one of the three tables."""
    names = set(db.table_names())
    parts = []
    for label, sql in _FINGERPRINT_SOURCES:
        table = "behavioral_insights" if label == "insights" else label
        if table not in names:
            parts.append(f"{label}:absent")
            continue
        try:
            row = db.execute(sql, [user_id]).fetchone()
        except Exception:  # noqa: BLE001 - an older catches table without the column
            parts.append(f"{label}:unreadable")
            continue
        parts.append(f"{label}:{row[0]}:{row[1]}")
    return "|".join(parts) + _SIGNALS_PART


def load(db: Database, user_id: int, fingerprint: str) -> UserLayer | None:
    """The stored layer, or None if absent or computed from different inputs."""
    # No table_names() probe — that is another query on the hot path, and a
    # missing table raises here just as a missing row does.
    try:
        row = db.execute(
            f"SELECT fingerprint, layer_json FROM {TABLE} WHERE user_id = ?",
            [user_id],
        ).fetchone()
    except Exception:  # noqa: BLE001 - no table yet is an ordinary cold cache
        return None
    if row is None:
        return None
    row = {"fingerprint": row[0], "layer_json": row[1]}
    if row.get("fingerprint") != fingerprint:
        logger.debug("user layer fingerprint changed for user %s", user_id)
        return None
    try:
        return UserLayer.model_validate_json(row["layer_json"])
    except Exception:  # noqa: BLE001 - a corrupt cache row is a miss, not a crash
        logger.warning(
            "Stored user layer for user %s could not be parsed; recomputing",
            user_id,
            exc_info=True,
        )
        return None


def store(db: Database, layer: UserLayer, fingerprint: str) -> None:
    """Persist a freshly computed layer. Never raises into the caller.

    A cache that cannot be written is a performance problem, not a correctness
    one — but it must not be a *silent* performance problem, because a
    permanently failing write is indistinguishable from a cold cache and the
    only symptom is everything being slow forever.
    """
    try:
        ensure_table(db)
        db[TABLE].upsert(
            {
                "user_id": layer.user_id,
                "fingerprint": fingerprint,
                "layer_json": layer.model_dump_json(),
                "computed_at": layer.computed_at,
            },
            pk="user_id",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not store derived user layer", exc_info=True)


def invalidate_all(db: Database) -> None:
    """Drop every stored layer. For a change to what the layers read besides the user's own
    rows (a spots rebuild changes which water a stop is on); costs time, not answers."""
    if TABLE not in db.table_names():
        return
    try:
        with db.conn:
            db.execute(f"DELETE FROM {TABLE}")
    except Exception:  # noqa: BLE001
        logger.warning("Could not invalidate derived user layers", exc_info=True)


def invalidate(db: Database, user_id: int) -> None:
    """Drop the stored layer so the next read recomputes."""
    if TABLE not in db.table_names():
        return
    try:
        db[TABLE].delete_where("user_id = ?", [user_id])
    except Exception:  # noqa: BLE001
        logger.warning("Could not invalidate derived user layer", exc_info=True)


def json_summary(layer: UserLayer) -> str:
    """Compact JSON for logging and the admin dashboard."""
    return json.dumps(
        {
            "user_id": layer.user_id,
            "sessions": layer.total_sessions,
            "stops": layer.total_stops,
            "orientation": layer.profile.orientation.value if layer.profile else None,
            "orientation_confidence": (
                layer.profile.orientation.confidence if layer.profile else None
            ),
            "patterns": len(layer.patterns),
            "computed_at": layer.computed_at,
        }
    )
