"""Persistence for weed reports: weed edges a user saw and wrote down.

Every function takes the acting user's id and filters on it. A report that
belongs to someone else is indistinguishable from one that does not exist:
lookups return None and writes return False, which the API turns into 404.
There is no function here that reads another user's rows, and none that counts
rows across users, so there is nothing for an endpoint to leak.

The report's reporter is `user_id`. Its jurisdiction is copied from the water
body it sits on when it is written.
"""

import json
from datetime import date, datetime

from sqlite_utils import Database

from src.models.weed_report import WeedGeometry, WeedReport

_COLUMNS = (
    "id, user_id, water_body_id, jurisdiction, geometry_geojson, observed_on, note, "
    "provenance, bbox_min_lng, bbox_min_lat, bbox_max_lng, bbox_max_lat, created_at, updated_at"
)


def ensure_tables(db: Database) -> None:
    """Create the weed_reports table. Idempotent."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS weed_reports (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            water_body_id    TEXT NOT NULL,
            jurisdiction     TEXT NOT NULL,
            geometry_geojson TEXT NOT NULL,
            observed_on      TEXT NOT NULL,
            note             TEXT,
            provenance       TEXT NOT NULL DEFAULT 'user_reported'
                             CHECK (provenance = 'user_reported'),
            bbox_min_lng     REAL NOT NULL,
            bbox_min_lat     REAL NOT NULL,
            bbox_max_lng     REAL NOT NULL,
            bbox_max_lat     REAL NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_weed_reports_user ON weed_reports(user_id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_weed_reports_user_water "
        "ON weed_reports(user_id, water_body_id)"
    )
    db.conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bbox(geometry: WeedGeometry) -> tuple[float, float, float, float]:
    points = [geometry.coordinates] if geometry.type == "Point" else geometry.coordinates
    lngs = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lngs), min(lats), max(lngs), max(lats)


def _dumps(geometry: WeedGeometry) -> str:
    return json.dumps(geometry.model_dump(), separators=(",", ":"), sort_keys=True)


def _from_row(r: dict) -> WeedReport:
    return WeedReport(
        id=r["id"],
        user_id=r["user_id"],
        water_body_id=r["water_body_id"],
        jurisdiction=r["jurisdiction"],
        geometry=WeedGeometry.model_validate(json.loads(r["geometry_geojson"])),
        observed_on=date.fromisoformat(r["observed_on"]),
        note=r["note"],
        provenance=r["provenance"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        bbox=(r["bbox_min_lng"], r["bbox_min_lat"], r["bbox_max_lng"], r["bbox_max_lat"]),
    )


def create_weed_report(
    db: Database,
    user_id: int,
    water_body_id: str,
    jurisdiction: str,
    geometry: WeedGeometry,
    observed_on: date,
    note: str | None = None,
) -> int:
    """Write a report. The caller has already checked it lies on the water body."""
    box = _bbox(geometry)
    cursor = db.execute(
        "INSERT INTO weed_reports (user_id, water_body_id, jurisdiction, geometry_geojson, "
        "observed_on, note, provenance, bbox_min_lng, bbox_min_lat, bbox_max_lng, bbox_max_lat) "
        "VALUES (?, ?, ?, ?, ?, ?, 'user_reported', ?, ?, ?, ?)",
        [
            user_id,
            water_body_id,
            jurisdiction,
            _dumps(geometry),
            observed_on.isoformat(),
            note or None,
            *box,
        ],
    )
    db.conn.commit()
    return cursor.lastrowid


def get_weed_report(db: Database, user_id: int, report_id: int) -> WeedReport | None:
    rows = db.execute_returning_dicts(
        f"SELECT {_COLUMNS} FROM weed_reports WHERE id = ? AND user_id = ?", [report_id, user_id]
    )
    return _from_row(rows[0]) if rows else None


def list_weed_reports(
    db: Database,
    user_id: int,
    water_body_id: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[WeedReport]:
    """The user's reports, newest sighting first. Optionally one water body and/or a box.

    A report is in a box when its own box overlaps (min_lon, min_lat, max_lon, max_lat).
    """
    clauses = ["user_id = ?"]
    params: list = [user_id]
    if water_body_id is not None:
        clauses.append("water_body_id = ?")
        params.append(water_body_id)
    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        clauses.append(
            "bbox_max_lng >= ? AND bbox_min_lng <= ? AND bbox_max_lat >= ? AND bbox_min_lat <= ?"
        )
        params += [min_lon, max_lon, min_lat, max_lat]
    rows = db.execute_returning_dicts(
        f"SELECT {_COLUMNS} FROM weed_reports WHERE {' AND '.join(clauses)} "
        "ORDER BY observed_on DESC, id DESC",
        params,
    )
    return [_from_row(r) for r in rows]


def update_weed_report(db: Database, user_id: int, report_id: int, fields: dict) -> bool:
    """Change geometry, observed_on or note. Unknown keys are ignored.

    `fields` carries only what changes; a `note` of None clears it. Returns
    False when the report is not this user's.
    """
    if get_weed_report(db, user_id, report_id) is None:
        return False
    sets: dict = {}
    if "geometry" in fields:
        geometry: WeedGeometry = fields["geometry"]
        sets["geometry_geojson"] = _dumps(geometry)
        (
            sets["bbox_min_lng"],
            sets["bbox_min_lat"],
            sets["bbox_max_lng"],
            sets["bbox_max_lat"],
        ) = _bbox(geometry)
    if "observed_on" in fields:
        sets["observed_on"] = fields["observed_on"].isoformat()
    if "note" in fields:
        sets["note"] = fields["note"] or None
    if not sets:
        return True
    assignments = ", ".join(f"{k} = ?" for k in sets)
    db.execute(
        f"UPDATE weed_reports SET {assignments}, updated_at = ? WHERE id = ? AND user_id = ?",
        [*sets.values(), _now(), report_id, user_id],
    )
    db.conn.commit()
    return True


def delete_weed_report(db: Database, user_id: int, report_id: int) -> bool:
    cursor = db.execute(
        "DELETE FROM weed_reports WHERE id = ? AND user_id = ?", [report_id, user_id]
    )
    db.conn.commit()
    return cursor.rowcount > 0
