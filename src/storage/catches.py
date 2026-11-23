"""Catch CRUD — one row per species caught at a stop.

Distinct from stops.species_caught (a JSON array kept for backward
compatibility with existing readers). catches gives each species its own
row so it can carry its own photo, count, size, and bait going forward.
"""

import json
from datetime import datetime
from typing import Any

from sqlite_utils.db import Database


def insert_catch(
    db: Database,
    *,
    stop_id: int,
    session_id: int,
    user_id: int,
    species: str,
    count: int | None = None,
    biggest_size: str | None = None,
    biggest_size_cm: float | None = None,
    bait: str | None = None,
    photo_path: str | None = None,
    photo_url: str | None = None,
    photo_lat: float | None = None,
    photo_lng: float | None = None,
    photo_taken_at: str | None = None,
    caught_at: str | None = None,
    species_confirmed: bool = True,
    suggested_species: list[dict[str, str]] | None = None,
) -> int:
    """Insert a catch row.

    species_confirmed defaults to True — direct inserts (tests, any future
    manual-entry path) represent already-trusted data. The NL-parsed/photo-
    suggested logging path (trip_logger.log_session) explicitly passes
    species_confirmed=False with suggested_species populated, since that's
    the one path where the species is a fallible AI suggestion rather than
    a human-entered fact — see the FishDex hallucination fix in fishdex.py.

    biggest_size_cm is the structured numeric size from the multi-catch
    logging UI (see trip_logger.parse_size_to_cm). biggest_size is the older
    free-text column — no current input path populates it; kept only so
    pre-existing rows that happen to have it keep displaying via fishdex.py's
    legacy fallback parse.

    caught_at is the user's actual tap time for the catch (the fast-tally
    logging UI's primary field), distinct from created_at, which is fixed to
    whenever the whole session's INSERT transaction runs at submit time.
    """
    return db["catches"].insert(
        {
            "stop_id": stop_id,
            "session_id": session_id,
            "user_id": user_id,
            "species": species,
            "count": count,
            "biggest_size": biggest_size,
            "biggest_size_cm": biggest_size_cm,
            "bait": bait,
            "photo_path": photo_path,
            "photo_url": photo_url,
            "photo_lat": photo_lat,
            "photo_lng": photo_lng,
            "photo_taken_at": photo_taken_at,
            "caught_at": caught_at,
            "species_confirmed": int(species_confirmed),
            "suggested_species": json.dumps(suggested_species) if suggested_species else None,
        }
    ).last_pk  # type: ignore[return-value]


def get_personal_best(db: Database, user_id: int, species: str) -> float | None:
    """Return the stored personal-best size (cm) for this user+species, or
    None if none is on record. Keyed the same way fishdex.py groups catches:
    raw species text, stripped and lowercased."""
    key = (species or "").strip().lower()
    if not key:
        return None
    row = next(
        iter(db.execute(
            "SELECT best_size_cm FROM personal_bests WHERE user_id = ? AND species = ?",
            [user_id, key],
        ).fetchall()),
        None,
    )
    return row[0] if row else None


def update_personal_best_if_higher(
    db: Database, *, user_id: int, species: str, size_cm: float, catch_id: int
) -> bool:
    """Update the stored personal-best size for this user+species if size_cm
    beats it (or none is on record yet). Returns whether it updated."""
    key = (species or "").strip().lower()
    if not key:
        return False
    current = get_personal_best(db, user_id, key)
    if current is not None and size_cm <= current:
        return False
    db.execute(
        """
        INSERT INTO personal_bests (user_id, species, best_size_cm, catch_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, species) DO UPDATE SET
            best_size_cm = excluded.best_size_cm,
            catch_id = excluded.catch_id,
            updated_at = excluded.updated_at
        """,
        [user_id, key, size_cm, catch_id, datetime.now().isoformat()],
    )
    db.conn.commit()
    return True


def get_all_catches_for_user(
    db: Database, user_id: int, confirmed_only: bool = False
) -> list[dict[str, Any]]:
    if confirmed_only:
        return list(
            db["catches"].rows_where(
                "user_id = ? AND species_confirmed = 1", [user_id], order_by="id"
            )
        )
    return list(db["catches"].rows_where("user_id = ?", [user_id], order_by="id"))


def get_pending_catches_for_user(db: Database, user_id: int) -> list[dict[str, Any]]:
    """Catches logged but not yet confirmed by the user — species_caught was
    parser/vision-suggested, not human-confirmed fact."""
    return list(
        db["catches"].rows_where(
            "user_id = ? AND species_confirmed = 0", [user_id], order_by="id desc"
        )
    )


def confirm_catch_species(
    db: Database, catch_id: int, user_id: int, species: str
) -> dict[str, bool]:
    """Commit the user's confirmed/corrected species.

    Returns {"found": bool, "is_new_pb": bool}. found is False if the catch
    doesn't exist or belongs to another user (is_new_pb is always False in
    that case). is_new_pb reflects whether confirming this catch's species
    just made it the new personal best for that species — the biggest/only
    detailed catch in a session is often still awaiting this call when the
    end-of-session summary card first renders, so the card needs this signal
    to surface a PB flag that only becomes knowable at confirm time.
    """
    from datetime import datetime

    row = next(iter(db["catches"].rows_where("id = ? AND user_id = ?", [catch_id, user_id])), None)
    if not row:
        return {"found": False, "is_new_pb": False}
    old_species_key = (row.get("species") or "").strip().lower()
    new_species_key = (species or "").strip().lower()
    db.execute(
        "UPDATE catches SET species = ?, species_confirmed = 1, confirmed_at = ? "
        "WHERE id = ? AND user_id = ?",
        [species, datetime.now().isoformat(), catch_id, user_id],
    )
    db.conn.commit()
    # The personal-best row recorded at insert time is keyed to whatever
    # placeholder species the NL parser/vision guessed then (e.g.
    # "unidentified fish sp."), not the species the user is confirming now —
    # get_fishdex_data only looks up personal_bests under the *confirmed*
    # species, so without this the PB silently never surfaces for any catch
    # that went through this flow.
    size_cm = row.get("biggest_size_cm")
    is_new_pb = False
    if size_cm is not None:
        is_new_pb = update_personal_best_if_higher(
            db, user_id=user_id, species=species, size_cm=size_cm, catch_id=catch_id
        )
    # Clean up the stale placeholder-species PB row this same catch created
    # at insert time, if the species actually changed on confirm (e.g. the
    # top vision guess "warmouth" reattributed to "smallmouth bass") — else
    # a phantom PB lingers for a species the user never actually confirmed
    # catching. Scoped to catch_id so a real, independently-earned PB under
    # the old species name (from some other catch) is never touched.
    if old_species_key and old_species_key != new_species_key:
        db.execute(
            "DELETE FROM personal_bests WHERE user_id = ? AND species = ? AND catch_id = ?",
            [user_id, old_species_key, catch_id],
        )
        db.conn.commit()
    return {"found": True, "is_new_pb": is_new_pb}


def get_catches_for_session(db: Database, session_id: int) -> list[dict[str, Any]]:
    return list(
        db["catches"].rows_where(
            "session_id = ?", [session_id], order_by="id"
        )
    )


def get_catches_for_sessions(
    db: Database, session_ids: list[int], *, user_id: int
) -> dict[int, list[dict[str, Any]]]:
    """Return {session_id: [catch, ...]} for a batch of sessions, one query."""
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    cursor = db.execute(
        f"SELECT * FROM catches WHERE user_id = ? AND session_id IN ({placeholders}) ORDER BY id",
        [user_id, *session_ids],
    )
    rows = cursor.fetchall()
    cols = [c[0] for c in cursor.description]
    by_session: dict[int, list[dict[str, Any]]] = {sid: [] for sid in session_ids}
    for r in rows:
        row = dict(zip(cols, r))
        by_session.setdefault(row["session_id"], []).append(row)
    return by_session
