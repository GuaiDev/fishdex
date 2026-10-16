"""Persistence for Personal-map pins, their links to logged stops, and
recalled "I've fished here before" records.

Every function takes the acting user's id and filters on it. A pin, stop or
session that belongs to someone else is indistinguishable from one that does
not exist: lookups return None and writes return False, which the API turns
into 404. There is no function here that reads another user's rows.

Three tables:

  * pins: the user's places. State (Saved/Visited) is derived on read.
  * pin_links: one row per linked stop. A stop is at one place, so it links
    to at most one pin (stop_id is the key). The trip's rating for that pin
    lives on these rows, one value per (session, pin).
  * pin_recalled_visits: recalled history. Kept out of stops and sessions so
    no statistic built on them can count a recalled visit.

Foreign keys are not enforced on this database (no PRAGMA foreign_keys), so
the ON DELETE behaviour is written out in the delete and merge functions
rather than declared and trusted.
"""

import json
import math
from datetime import datetime

from sqlite_utils import Database

from src.jurisdictions.geo import jurisdiction_for_coords


def ensure_tables(db: Database) -> None:
    """Create the pin tables and the trip columns pins need. Idempotent."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS pins (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            name            TEXT NOT NULL,
            lat             REAL NOT NULL,
            lng             REAL NOT NULL,
            jurisdiction    TEXT NOT NULL,
            is_favourite    INTEGER NOT NULL DEFAULT 0,
            notes           TEXT,
            provenance      TEXT NOT NULL DEFAULT 'user'
                            CHECK (provenance IN ('user', 'imported')),
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pins_user ON pins(user_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS pin_links (
            stop_id         INTEGER PRIMARY KEY,
            pin_id          INTEGER NOT NULL,
            session_id      INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            link_kind       TEXT NOT NULL CHECK (link_kind IN ('confirmed', 'inferred')),
            distance_m      REAL,
            rating          INTEGER CHECK (rating BETWEEN 1 AND 5),
            linked_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pin_links_pin ON pin_links(pin_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pin_links_session ON pin_links(session_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS pin_recalled_visits (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pin_id          INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            date_approx     TEXT,
            species         TEXT NOT NULL DEFAULT '[]',
            rating          INTEGER CHECK (rating BETWEEN 1 AND 5),
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pin_recalled_pin ON pin_recalled_visits(pin_id)")

    session_columns = {c.name for c in db["sessions"].columns}
    for name, decl in (
        # NULL = not rated. Never read as a low rating.
        ("rating", "INTEGER CHECK (rating BETWEEN 1 AND 5)"),
        # From the "Start fishing" timer, editable later. Trips logged before
        # these columns existed have neither, and fish per hour shows "—".
        ("started_at", "TEXT"),
        ("ended_at", "TEXT"),
    ):
        if name not in session_columns:
            db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {decl}")

    # Catches stay queryable by species for a later per-species filter.
    db.execute("CREATE INDEX IF NOT EXISTS idx_catches_user_species ON catches(user_id, species)")
    db.conn.commit()


# ── geometry ─────────────────────────────────────────────────────────────────


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def stop_point(stop: dict) -> tuple[float, float] | None:
    """Where a stop is: photo coordinates first, then parsed or device ones.

    The same preference the Personal map has always drawn stops with.
    """
    if stop.get("photo_lat") is not None and stop.get("photo_lng") is not None:
        return stop["photo_lat"], stop["photo_lng"]
    if stop.get("lat") is not None and stop.get("lng") is not None:
        return stop["lat"], stop["lng"]
    return None


def _rows(db: Database, sql: str, params: list) -> list[dict]:
    cursor = db.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── pins ─────────────────────────────────────────────────────────────────────

_PIN_COLUMNS = (
    "id, user_id, name, lat, lng, jurisdiction, is_favourite, notes, provenance, "
    "created_at, updated_at"
)


def create_pin(
    db: Database,
    user_id: int,
    name: str,
    lat: float,
    lng: float,
    notes: str | None = None,
    is_favourite: bool = False,
    provenance: str = "user",
) -> int:
    cursor = db.execute(
        "INSERT INTO pins (user_id, name, lat, lng, jurisdiction, is_favourite, notes, "
        "provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            user_id,
            name,
            lat,
            lng,
            jurisdiction_for_coords(lat, lng),
            1 if is_favourite else 0,
            notes or None,
            provenance,
        ],
    )
    db.conn.commit()
    return cursor.lastrowid


def get_pin(db: Database, user_id: int, pin_id: int) -> dict | None:
    rows = _rows(
        db, f"SELECT {_PIN_COLUMNS} FROM pins WHERE id = ? AND user_id = ?", [pin_id, user_id]
    )
    return rows[0] if rows else None


def list_pins(db: Database, user_id: int) -> list[dict]:
    """The user's pins with what the map needs to draw and label each one.

    last_visit is the newest dated logged trip. Recalled records never count
    toward it, so a recalled-only pin has no last visit.
    """
    return _rows(
        db,
        f"""
        SELECT {", ".join("p." + c.strip() for c in _PIN_COLUMNS.split(","))},
            (SELECT COUNT(DISTINCT pl.session_id) FROM pin_links pl
              WHERE pl.pin_id = p.id AND pl.user_id = p.user_id) AS visits,
            (SELECT COUNT(*) FROM pin_recalled_visits r
              WHERE r.pin_id = p.id AND r.user_id = p.user_id) AS recalled_count,
            (SELECT MAX(s.date) FROM pin_links pl
               JOIN sessions s ON s.id = pl.session_id AND s.user_id = pl.user_id
              WHERE pl.pin_id = p.id AND pl.user_id = p.user_id) AS last_visit
        FROM pins p
        WHERE p.user_id = ?
        ORDER BY p.is_favourite DESC, p.name COLLATE NOCASE
        """,
        [user_id],
    )


def update_pin(db: Database, user_id: int, pin_id: int, fields: dict) -> bool:
    """Change name, notes or favourite. Unknown keys are ignored."""
    allowed = {k: v for k, v in fields.items() if k in ("name", "notes", "is_favourite")}
    if get_pin(db, user_id, pin_id) is None:
        return False
    if not allowed:
        return True
    if "is_favourite" in allowed:
        allowed["is_favourite"] = 1 if allowed["is_favourite"] else 0
    if "notes" in allowed:
        allowed["notes"] = allowed["notes"] or None
    assignments = ", ".join(f"{k} = ?" for k in allowed)
    db.execute(
        f"UPDATE pins SET {assignments}, updated_at = ? WHERE id = ? AND user_id = ?",
        [*allowed.values(), _now(), pin_id, user_id],
    )
    db.conn.commit()
    return True


def delete_pin(db: Database, user_id: int, pin_id: int) -> bool:
    """Delete a pin and its recalled records. Linked trips are unlinked, never deleted."""
    if get_pin(db, user_id, pin_id) is None:
        return False
    with db.conn:
        db.execute("DELETE FROM pin_links WHERE pin_id = ? AND user_id = ?", [pin_id, user_id])
        db.execute(
            "DELETE FROM pin_recalled_visits WHERE pin_id = ? AND user_id = ?", [pin_id, user_id]
        )
        db.execute("DELETE FROM pins WHERE id = ? AND user_id = ?", [pin_id, user_id])
    return True


def merge_pins(db: Database, user_id: int, from_pin_id: int, into_pin_id: int) -> bool:
    """Move everything on from_pin to into_pin, then delete from_pin.

    Links and recalled records move. Where one trip was linked to both pins,
    its rating at the survivor is the survivor's if it had one, otherwise the
    merged pin's. The survivor keeps its name; it is a favourite if either
    was; notes are kept from both.
    """
    if from_pin_id == into_pin_id:
        return False
    source = get_pin(db, user_id, from_pin_id)
    target = get_pin(db, user_id, into_pin_id)
    if source is None or target is None:
        return False

    shared_ratings = _rows(
        db,
        """
        SELECT a.session_id,
               (SELECT MAX(rating) FROM pin_links
                 WHERE session_id = a.session_id AND pin_id = ? AND user_id = ?) AS into_rating,
               (SELECT MAX(rating) FROM pin_links
                 WHERE session_id = a.session_id AND pin_id = ? AND user_id = ?) AS from_rating
        FROM (SELECT DISTINCT session_id FROM pin_links WHERE pin_id = ? AND user_id = ?) a
        WHERE a.session_id IN (SELECT session_id FROM pin_links WHERE pin_id = ? AND user_id = ?)
        """,
        [
            into_pin_id,
            user_id,
            from_pin_id,
            user_id,
            from_pin_id,
            user_id,
            into_pin_id,
            user_id,
        ],
    )

    notes = [n for n in (target["notes"], source["notes"]) if n]
    merged_notes = "\n\n".join(dict.fromkeys(notes)) or None

    with db.conn:
        db.execute(
            "UPDATE pin_links SET pin_id = ? WHERE pin_id = ? AND user_id = ?",
            [into_pin_id, from_pin_id, user_id],
        )
        for row in shared_ratings:
            rating = row["into_rating"] if row["into_rating"] is not None else row["from_rating"]
            db.execute(
                "UPDATE pin_links SET rating = ? "
                "WHERE session_id = ? AND pin_id = ? AND user_id = ?",
                [rating, row["session_id"], into_pin_id, user_id],
            )
        db.execute(
            "UPDATE pin_recalled_visits SET pin_id = ? WHERE pin_id = ? AND user_id = ?",
            [into_pin_id, from_pin_id, user_id],
        )
        db.execute(
            "UPDATE pins SET is_favourite = ?, notes = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            [
                1 if (target["is_favourite"] or source["is_favourite"]) else 0,
                merged_notes,
                _now(),
                into_pin_id,
                user_id,
            ],
        )
        db.execute("DELETE FROM pins WHERE id = ? AND user_id = ?", [from_pin_id, user_id])
    return True


def pins_within(db: Database, user_id: int, lat: float, lng: float, radius_m: float) -> list[dict]:
    """The user's pins within radius_m of a point, nearest first, with distance_m."""
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    candidates = _rows(
        db,
        f"SELECT {_PIN_COLUMNS} FROM pins WHERE user_id = ? "
        "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
        [user_id, lat - dlat, lat + dlat, lng - dlng, lng + dlng],
    )
    near = []
    for pin in candidates:
        d = distance_m(lat, lng, pin["lat"], pin["lng"])
        if d <= radius_m:
            near.append({**pin, "distance_m": round(d, 1)})
    return sorted(near, key=lambda p: p["distance_m"])


# ── stops and links ──────────────────────────────────────────────────────────

_STOP_COLUMNS = (
    "st.id, st.session_id, st.lat, st.lng, st.photo_lat, st.photo_lng, "
    "st.location_name, st.location_text, st.species_caught, st.was_productive, "
    "st.technique, st.gear, s.date, s.date_approx"
)


def get_stop(db: Database, user_id: int, stop_id: int) -> dict | None:
    rows = _rows(
        db,
        f"SELECT {_STOP_COLUMNS} FROM stops st "
        "JOIN sessions s ON s.id = st.session_id AND s.user_id = st.user_id "
        "WHERE st.id = ? AND st.user_id = ?",
        [stop_id, user_id],
    )
    return rows[0] if rows else None


def session_stops(db: Database, user_id: int, session_id: int) -> list[dict]:
    return _rows(
        db,
        f"SELECT {_STOP_COLUMNS} FROM stops st "
        "JOIN sessions s ON s.id = st.session_id AND s.user_id = st.user_id "
        "WHERE st.session_id = ? AND st.user_id = ? ORDER BY st.id",
        [session_id, user_id],
    )


def unpinned_stops(db: Database, user_id: int) -> list[dict]:
    """The user's located stops that no pin links to, newest first.

    session_conditions is joined through the user's own session, not on its
    user_id: enrichment never writes that column, so every row carries the
    default user 1. session_id is unique there, so the join cannot cross users.
    """
    rows = _rows(
        db,
        f"SELECT {_STOP_COLUMNS}, sc.air_temp_c, sc.pressure_hpa, sc.anomaly_flag "
        "FROM stops st "
        "JOIN sessions s ON s.id = st.session_id AND s.user_id = st.user_id "
        "LEFT JOIN session_conditions sc ON sc.session_id = s.id "
        "LEFT JOIN pin_links pl ON pl.stop_id = st.id AND pl.user_id = st.user_id "
        "WHERE st.user_id = ? AND pl.stop_id IS NULL "
        "AND (st.lat IS NOT NULL OR st.photo_lat IS NOT NULL) "
        "ORDER BY st.session_id DESC, st.id DESC",
        [user_id],
    )
    return [r for r in rows if stop_point(r) is not None]


def link_for_stop(db: Database, user_id: int, stop_id: int) -> dict | None:
    rows = _rows(
        db,
        "SELECT stop_id, pin_id, session_id, link_kind, distance_m, rating "
        "FROM pin_links WHERE stop_id = ? AND user_id = ?",
        [stop_id, user_id],
    )
    return rows[0] if rows else None


def _inherited_rating(db: Database, user_id: int, session_id: int, pin_id: int, stop_id: int):
    """The rating a new link starts with.

    Another stop of the same trip already at this pin: that trip-pin rating.
    The trip touched no pin until now: the trip's own rating, because a
    single-pin trip's rating is that pin's rating. Otherwise the trip now
    spans several pins and this one has not been asked about: None.
    """
    same_pair = db.execute(
        "SELECT COUNT(*), MAX(rating) FROM pin_links "
        "WHERE session_id = ? AND pin_id = ? AND user_id = ? AND stop_id != ?",
        [session_id, pin_id, user_id, stop_id],
    ).fetchone()
    if same_pair[0]:
        return same_pair[1]
    other_pins = db.execute(
        "SELECT COUNT(*) FROM pin_links WHERE session_id = ? AND user_id = ? AND stop_id != ?",
        [session_id, user_id, stop_id],
    ).fetchone()[0]
    if other_pins:
        return None
    row = db.execute(
        "SELECT rating FROM sessions WHERE id = ? AND user_id = ?", [session_id, user_id]
    ).fetchone()
    return row[0] if row else None


def link_stop(db: Database, user_id: int, pin_id: int, stop_id: int, kind: str) -> dict | None:
    """Link one of the user's stops to one of their pins.

    A stop already linked elsewhere moves. Returns the link, or None when
    either the pin or the stop is not this user's.
    """
    pin = get_pin(db, user_id, pin_id)
    stop = get_stop(db, user_id, stop_id)
    if pin is None or stop is None:
        return None
    point = stop_point(stop)
    dist = round(distance_m(point[0], point[1], pin["lat"], pin["lng"]), 1) if point else None
    rating = _inherited_rating(db, user_id, stop["session_id"], pin_id, stop_id)
    db.execute(
        "INSERT OR REPLACE INTO pin_links "
        "(stop_id, pin_id, session_id, user_id, link_kind, distance_m, rating, linked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [stop_id, pin_id, stop["session_id"], user_id, kind, dist, rating, _now()],
    )
    db.conn.commit()
    return link_for_stop(db, user_id, stop_id)


def unlink_stop(db: Database, user_id: int, pin_id: int, stop_id: int) -> bool:
    cursor = db.execute(
        "DELETE FROM pin_links WHERE stop_id = ? AND pin_id = ? AND user_id = ?",
        [stop_id, pin_id, user_id],
    )
    db.conn.commit()
    return cursor.rowcount > 0


def pin_link_rows(db: Database, user_id: int, pin_id: int) -> list[dict]:
    """Every stop linked to this pin, with its trip's date, times and rating."""
    return _rows(
        db,
        """
        SELECT pl.stop_id, pl.session_id, pl.link_kind, pl.distance_m, pl.rating,
               st.location_name, st.location_text,
               s.date, s.date_approx, s.started_at, s.ended_at
        FROM pin_links pl
        JOIN stops st ON st.id = pl.stop_id AND st.user_id = pl.user_id
        JOIN sessions s ON s.id = pl.session_id AND s.user_id = pl.user_id
        WHERE pl.pin_id = ? AND pl.user_id = ?
        ORDER BY COALESCE(s.date, '') DESC, pl.session_id DESC, pl.stop_id
        """,
        [pin_id, user_id],
    )


def pin_catches(db: Database, user_id: int, pin_id: int) -> list[dict]:
    """Catches logged at stops linked to this pin. count may be NULL."""
    return _rows(
        db,
        """
        SELECT c.id, c.session_id, c.stop_id, c.species, c.count, c.biggest_size_cm,
               c.species_confirmed
        FROM catches c
        JOIN pin_links pl ON pl.stop_id = c.stop_id AND pl.user_id = c.user_id
        WHERE pl.pin_id = ? AND pl.user_id = ?
        """,
        [pin_id, user_id],
    )


# Besides the readings the caller asks for, the match needs to know which gauge
# a snapshot came from and when the water sample behind a trip was taken.
_TRIP_SNAPSHOT_COLUMNS = ("gauge_station_id", "gauge_station_name", "pwqmn_sampled_at")


def rated_trips(
    db: Database, user_id: int, pin_id: int, condition_columns: tuple[str, ...]
) -> list[dict]:
    """The user's trips at this pin that they rated, one row per trip, with conditions.

    A trip is rated at a pin when a stop of it links to the pin and that link
    carries a rating, so recalled visits (a different table) can never appear.
    A trip with several stops here is one row. Conditions are the trip's own
    session_conditions row, read through the user's own session: that table's
    user_id was not reliably written, so the join goes by session. Columns the
    database does not have yet (an older schema) read as NULL, and a trip with
    no conditions row has every reading NULL. Newest trip first.
    """
    have: set[str] = set()
    if "session_conditions" in db.table_names():
        have = {c.name for c in db["session_conditions"].columns}
    wanted = [*condition_columns, *_TRIP_SNAPSHOT_COLUMNS]
    select = ", ".join(f"sc.{c}" if c in have else f"NULL AS {c}" for c in wanted)
    return _rows(
        db,
        f"""
        SELECT pl.session_id, MAX(pl.rating) AS rating, s.date, {select}
        FROM pin_links pl
        JOIN sessions s ON s.id = pl.session_id AND s.user_id = pl.user_id
        LEFT JOIN session_conditions sc ON sc.session_id = s.id
        WHERE pl.pin_id = ? AND pl.user_id = ? AND pl.rating IS NOT NULL
        GROUP BY pl.session_id
        ORDER BY COALESCE(s.date, '') DESC, pl.session_id DESC
        """,
        [pin_id, user_id],
    )


def session_stop_counts(db: Database, user_id: int, session_ids: list[int]) -> dict[int, int]:
    if not session_ids:
        return {}
    marks = ",".join("?" * len(session_ids))
    return {
        sid: n
        for sid, n in db.execute(
            f"SELECT session_id, COUNT(*) FROM stops WHERE user_id = ? AND session_id IN ({marks}) "
            "GROUP BY session_id",
            [user_id, *session_ids],
        ).fetchall()
    }


def session_catch_totals(db: Database, user_id: int, session_ids: list[int]) -> dict[int, int]:
    """Fish per session, a missing count read as one fish."""
    if not session_ids:
        return {}
    marks = ",".join("?" * len(session_ids))
    return {
        sid: n
        for sid, n in db.execute(
            f"SELECT session_id, SUM(COALESCE(count, 1)) FROM catches "
            f"WHERE user_id = ? AND session_id IN ({marks}) GROUP BY session_id",
            [user_id, *session_ids],
        ).fetchall()
    }


def pins_for_sessions(db: Database, user_id: int, session_ids: list[int]) -> dict[int, list[dict]]:
    """For each session, the pins its stops link to and the trip's rating at each."""
    if not session_ids:
        return {}
    marks = ",".join("?" * len(session_ids))
    out: dict[int, list[dict]] = {}
    for row in _rows(
        db,
        f"""
        SELECT pl.session_id, p.id AS pin_id, p.name, MAX(pl.rating) AS rating
        FROM pin_links pl
        JOIN pins p ON p.id = pl.pin_id AND p.user_id = pl.user_id
        WHERE pl.user_id = ? AND pl.session_id IN ({marks})
        GROUP BY pl.session_id, p.id
        ORDER BY MIN(pl.stop_id)
        """,
        [user_id, *session_ids],
    ):
        out.setdefault(row["session_id"], []).append(
            {"pin_id": row["pin_id"], "name": row["name"], "rating": row["rating"]}
        )
    return out


# ── trips: rating and times ──────────────────────────────────────────────────


def get_session(db: Database, user_id: int, session_id: int) -> dict | None:
    rows = _rows(
        db,
        "SELECT id, date, date_approx, rating, started_at, ended_at FROM sessions "
        "WHERE id = ? AND user_id = ?",
        [session_id, user_id],
    )
    return rows[0] if rows else None


def update_session(db: Database, user_id: int, session_id: int, fields: dict) -> bool:
    """Set the trip's rating and/or start and end times.

    A new trip rating also becomes the pin rating when the trip touches
    exactly one pin. A trip across several pins is rated per pin instead
    (set_trip_pin_rating), so its pin ratings are left as they are.
    """
    allowed = {k: v for k, v in fields.items() if k in ("rating", "started_at", "ended_at")}
    if get_session(db, user_id, session_id) is None:
        return False
    if not allowed:
        return True
    assignments = ", ".join(f"{k} = ?" for k in allowed)
    with db.conn:
        db.execute(
            f"UPDATE sessions SET {assignments} WHERE id = ? AND user_id = ?",
            [*allowed.values(), session_id, user_id],
        )
        if "rating" in allowed:
            pin_count = db.execute(
                "SELECT COUNT(DISTINCT pin_id) FROM pin_links WHERE session_id = ? AND user_id = ?",
                [session_id, user_id],
            ).fetchone()[0]
            if pin_count == 1:
                db.execute(
                    "UPDATE pin_links SET rating = ? WHERE session_id = ? AND user_id = ?",
                    [allowed["rating"], session_id, user_id],
                )
    return True


def set_trip_pin_rating(
    db: Database, user_id: int, session_id: int, pin_id: int, rating: int | None
) -> bool:
    """Rate one pin of a trip. False when the trip is not linked to that pin."""
    cursor = db.execute(
        "UPDATE pin_links SET rating = ? WHERE session_id = ? AND pin_id = ? AND user_id = ?",
        [rating, session_id, pin_id, user_id],
    )
    db.conn.commit()
    return cursor.rowcount > 0


# ── recalled visits ──────────────────────────────────────────────────────────


def add_recalled_visit(
    db: Database,
    user_id: int,
    pin_id: int,
    date_approx: str | None,
    species: list[str],
    rating: int | None,
) -> int | None:
    if get_pin(db, user_id, pin_id) is None:
        return None
    cursor = db.execute(
        "INSERT INTO pin_recalled_visits (pin_id, user_id, date_approx, species, rating) "
        "VALUES (?, ?, ?, ?, ?)",
        [pin_id, user_id, date_approx or None, json.dumps(species), rating],
    )
    db.conn.commit()
    return cursor.lastrowid


def list_recalled_visits(db: Database, user_id: int, pin_id: int) -> list[dict]:
    rows = _rows(
        db,
        "SELECT id, pin_id, date_approx, species, rating, created_at "
        "FROM pin_recalled_visits WHERE pin_id = ? AND user_id = ? ORDER BY id DESC",
        [pin_id, user_id],
    )
    for row in rows:
        row["species"] = json.loads(row["species"] or "[]")
    return rows


def delete_recalled_visit(db: Database, user_id: int, pin_id: int, recalled_id: int) -> bool:
    cursor = db.execute(
        "DELETE FROM pin_recalled_visits WHERE id = ? AND pin_id = ? AND user_id = ?",
        [recalled_id, pin_id, user_id],
    )
    db.conn.commit()
    return cursor.rowcount > 0
