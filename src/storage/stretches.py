"""Fishing stretch storage: Level 1 of the explore map.

A build replaces a jurisdiction's stretches wholesale, in one transaction. A
stretch removed from the curation file must disappear from the map, and a
half-written rebuild must never be what the map reads.
"""

import json

from sqlite_utils.db import Database

from src.models.stretch import FishingStretch, StretchSegment


def replace_stretches(
    db: Database,
    jurisdiction: str,
    stretches: list[FishingStretch],
    segments: list[StretchSegment],
) -> int:
    """Swap in a jurisdiction's stretches. Returns the stretch rows now stored.

    Plain executemany inside one `with conn`, not insert_all: sqlite_utils opens
    its own `with conn` per call, and the inner block's commit would land the
    DELETEs before the INSERTs — a window where the map reads no stretches.
    """
    stretch_rows = [_stretch_row(s) for s in stretches]
    segment_rows = [s.model_dump() for s in segments]
    conn = db.conn
    with conn:
        conn.execute("DELETE FROM stretch_segments WHERE jurisdiction = ?", [jurisdiction])
        conn.execute("DELETE FROM fishing_stretches WHERE jurisdiction = ?", [jurisdiction])
        if stretch_rows:
            cols = list(stretch_rows[0])
            conn.executemany(
                f"INSERT INTO fishing_stretches ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                stretch_rows,
            )
        if segment_rows:
            conn.executemany(
                "INSERT INTO stretch_segments (ogf_id, stretch_id, jurisdiction, seq) "
                "VALUES (:ogf_id, :stretch_id, :jurisdiction, :seq)",
                segment_rows,
            )
    return count_stretches(db, jurisdiction)


def count_stretches(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM fishing_stretches").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM fishing_stretches WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_stretches(db: Database, jurisdiction: str | None = None) -> list[FishingStretch]:
    where, params = ("WHERE jurisdiction = ?", [jurisdiction]) if jurisdiction else ("", [])
    rows = db.execute_returning_dicts(
        f"SELECT * FROM fishing_stretches {where} ORDER BY jurisdiction, sort_order, stretch_id",
        params,
    )
    return [_row_to_stretch(r) for r in rows]


def stretch_segment_ids(db: Database, stretch_id: str) -> list[int]:
    return [
        r[0]
        for r in db.execute(
            "SELECT ogf_id FROM stretch_segments WHERE stretch_id = ? ORDER BY seq",
            [stretch_id],
        ).fetchall()
    ]


def _stretch_row(s: FishingStretch) -> dict:
    min_lng, min_lat, max_lng, max_lat = s.bbox
    return {
        "stretch_id": s.stretch_id,
        "name": s.name,
        "river": s.river,
        "region": s.region,
        "jurisdiction": s.jurisdiction,
        "length_km": s.length_km,
        "segment_count": s.segment_count,
        "geometry_geojson": json.dumps(s.geometry, separators=(",", ":")),
        "bbox_min_lng": min_lng,
        "bbox_min_lat": min_lat,
        "bbox_max_lng": max_lng,
        "bbox_max_lat": max_lat,
        "centroid_lat": s.centroid_lat,
        "centroid_lng": s.centroid_lon,
        "sort_order": s.sort_order,
        "notes": s.notes,
        "source": s.source,
        "built_at": s.built_at,
    }


def _row_to_stretch(r: dict) -> FishingStretch:
    return FishingStretch(
        stretch_id=r["stretch_id"],
        name=r["name"],
        river=r["river"],
        region=r["region"],
        jurisdiction=r["jurisdiction"],
        length_km=r["length_km"],
        segment_count=r["segment_count"],
        geometry=json.loads(r["geometry_geojson"]),
        bbox=(r["bbox_min_lng"], r["bbox_min_lat"], r["bbox_max_lng"], r["bbox_max_lat"]),
        centroid_lat=r["centroid_lat"],
        centroid_lon=r["centroid_lng"],
        sort_order=r["sort_order"],
        notes=r["notes"],
        source=r["source"],
        built_at=r["built_at"],
    )
