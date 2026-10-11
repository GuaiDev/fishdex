"""Spot storage: the explore map's tap targets.

A rebuild replaces a jurisdiction's spots wholesale, in one transaction, for the
same reason anchors are replaced that way: a spot that stopped being derived
must leave the map, and the map must never read a half-written rebuild. Spots
hold nothing per-user; anything personal is joined at read time.
"""

import json

from sqlite_utils.db import Database

from src.models.spot import Spot


def replace_spots(db: Database, jurisdiction: str, spots: list[Spot]) -> int:
    """Swap in a jurisdiction's spots. Returns spots now stored."""
    rows = [_spot_row(s) for s in spots]
    conn = db.conn
    with conn:
        conn.execute("DELETE FROM spots WHERE jurisdiction = ?", [jurisdiction])
        if rows:
            cols = list(rows[0])
            conn.executemany(
                f"INSERT INTO spots ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                rows,
            )
    return count_spots(db, jurisdiction)


def count_spots(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM spots").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM spots WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_spots(
    db: Database,
    bbox: tuple[float, float, float, float] | None = None,
    max_tier: int = 3,
    jurisdiction: str | None = None,
    water_id: str | None = None,
) -> list[Spot]:
    """Spots in a (min_lon, min_lat, max_lon, max_lat) box, up to a tier, in id order."""
    clauses, params = ["tier <= ?"], [max_tier]
    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        clauses.append("lng BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
        params += [min_lon, max_lon, min_lat, max_lat]
    if jurisdiction is not None:
        clauses.append("jurisdiction = ?")
        params.append(jurisdiction)
    if water_id is not None:
        clauses.append("water_id = ?")
        params.append(water_id)
    rows = db.execute_returning_dicts(
        f"SELECT * FROM spots WHERE {' AND '.join(clauses)} ORDER BY spot_id", params
    )
    return [_row_to_spot(r) for r in rows]


def get_spot(db: Database, spot_id: str) -> Spot | None:
    rows = db.execute_returning_dicts("SELECT * FROM spots WHERE spot_id = ?", [spot_id])
    return _row_to_spot(rows[0]) if rows else None


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _spot_row(s: Spot) -> dict:
    return {
        "spot_id": s.spot_id,
        "kind": s.kind,
        "jurisdiction": s.jurisdiction,
        "lat": s.lat,
        "lng": s.lng,
        "water_id": s.water_id,
        "water_name": s.water_name,
        "title": s.title,
        "subtitle": s.subtitle,
        "tier": s.tier,
        "structure_type": s.structure_type,
        "structure_attrs_json": _dumps(s.structure_attrs),
        "signals_json": _dumps(s.signals),
        "getting_there_json": _dumps([r.model_dump() for r in s.getting_there]),
        "bridges_json": _dumps([r.model_dump() for r in s.bridges]),
        "sources_json": _dumps([r.model_dump() for r in s.sources]),
        "built_at": s.built_at,
    }


def _row_to_spot(r: dict) -> Spot:
    return Spot(
        spot_id=r["spot_id"],
        kind=r["kind"],
        jurisdiction=r["jurisdiction"],
        lat=r["lat"],
        lng=r["lng"],
        water_id=r["water_id"],
        water_name=r["water_name"],
        title=r["title"],
        subtitle=r["subtitle"],
        tier=r["tier"],
        structure_type=r["structure_type"],
        structure_attrs=json.loads(r["structure_attrs_json"]),
        signals=json.loads(r["signals_json"]),
        getting_there=json.loads(r["getting_there_json"]),
        bridges=json.loads(r["bridges_json"]),
        sources=json.loads(r["sources_json"]),
        built_at=r["built_at"],
    )
