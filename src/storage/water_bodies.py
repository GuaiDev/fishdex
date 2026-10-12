"""Water body storage: the rivers, creeks and lakes spots hang off.

Rebuilt wholesale with the spots, in the same transaction (see
storage/spots.py::replace_spots), so a map never reads spots that point at a
water body the rebuild removed.
"""

import json
import sqlite3

from sqlite_utils.db import Database

from src.models.water_body import WaterBody


def write_water_bodies(
    conn: sqlite3.Connection, jurisdiction: str, bodies: list[WaterBody]
) -> None:
    """Swap in a jurisdiction's water bodies on an open connection. The caller commits."""
    conn.execute("DELETE FROM water_bodies WHERE jurisdiction = ?", [jurisdiction])
    rows = [_row(b) for b in bodies]
    if rows:
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO water_bodies ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})",
            rows,
        )


def count_water_bodies(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM water_bodies").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM water_bodies WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_water_bodies(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    types: list[str] | None = None,
    jurisdiction: str | None = None,
) -> list[WaterBody]:
    """Water bodies whose box overlaps (min_lon, min_lat, max_lon, max_lat), in id order."""
    clauses: list[str] = []
    params: list = []
    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        clauses.append(
            "bbox_max_lng >= ? AND bbox_min_lng <= ? AND bbox_max_lat >= ? AND bbox_min_lat <= ?"
        )
        params += [min_lon, max_lon, min_lat, max_lat]
    if types:
        clauses.append(f"type IN ({', '.join('?' * len(types))})")
        params += types
    if jurisdiction is not None:
        clauses.append("jurisdiction = ?")
        params.append(jurisdiction)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute_returning_dicts(
        f"SELECT * FROM water_bodies {where} ORDER BY water_body_id", params
    )
    return [_from_row(r) for r in rows]


def get_water_body(db: Database, water_body_id: str) -> WaterBody | None:
    rows = db.execute_returning_dicts(
        "SELECT * FROM water_bodies WHERE water_body_id = ?", [water_body_id]
    )
    return _from_row(rows[0]) if rows else None


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _row(b: WaterBody) -> dict:
    box = b.bbox or (None, None, None, None)
    return {
        "water_body_id": b.water_body_id,
        "jurisdiction": b.jurisdiction,
        "name": b.name,
        "name_source": b.name_source,
        "type": b.type,
        "geometry_geojson": _dumps(b.geometry) if b.geometry else None,
        "geometry_source": b.geometry_source,
        "bbox_min_lng": box[0],
        "bbox_min_lat": box[1],
        "bbox_max_lng": box[2],
        "bbox_max_lat": box[3],
        "stretch_ids_json": _dumps(b.stretch_ids),
        "attributes_json": _dumps(b.attributes),
        "built_at": b.built_at,
    }


def _from_row(r: dict) -> WaterBody:
    box = (r["bbox_min_lng"], r["bbox_min_lat"], r["bbox_max_lng"], r["bbox_max_lat"])
    return WaterBody(
        water_body_id=r["water_body_id"],
        jurisdiction=r["jurisdiction"],
        name=r["name"],
        name_source=r["name_source"],
        type=r["type"],
        geometry=json.loads(r["geometry_geojson"]) if r["geometry_geojson"] else None,
        geometry_source=r["geometry_source"],
        bbox=box if None not in box else None,
        stretch_ids=json.loads(r["stretch_ids_json"]),
        attributes=json.loads(r["attributes_json"]),
        built_at=r["built_at"],
    )
