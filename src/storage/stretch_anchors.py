"""Stretch anchor storage: Level 2 of the explore map.

A rebuild replaces a jurisdiction's anchors and segment lines wholesale, in one
transaction, for the same reason stretches are replaced that way: an anchor
that stopped being derived must leave the map, and the map must never read a
half-written rebuild. replace_stretches (storage/stretches.py) removes the
anchors of any stretch it drops.
"""

import json

from sqlite_utils.db import Database

from src.models.stretch_anchor import StretchAnchor, StretchSegmentLine


def replace_anchors(
    db: Database,
    jurisdiction: str,
    anchors: list[StretchAnchor],
    lines: list[StretchSegmentLine],
) -> int:
    """Swap in a jurisdiction's anchors and segment lines. Returns anchors now stored.

    Plain executemany inside one `with conn`, as in replace_stretches.
    """
    anchor_rows = [_anchor_row(a) for a in anchors]
    line_rows = [_line_row(line) for line in lines]
    conn = db.conn
    with conn:
        conn.execute("DELETE FROM stretch_anchors WHERE jurisdiction = ?", [jurisdiction])
        conn.execute("DELETE FROM stretch_segment_lines WHERE jurisdiction = ?", [jurisdiction])
        if anchor_rows:
            cols = list(anchor_rows[0])
            conn.executemany(
                f"INSERT INTO stretch_anchors ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                anchor_rows,
            )
        if line_rows:
            cols = list(line_rows[0])
            conn.executemany(
                f"INSERT INTO stretch_segment_lines ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})",
                line_rows,
            )
    return count_anchors(db, jurisdiction)


def count_anchors(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM stretch_anchors").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM stretch_anchors WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_anchors(
    db: Database, stretch_id: str | None = None, jurisdiction: str | None = None
) -> list[StretchAnchor]:
    clauses, params = [], []
    if stretch_id is not None:
        clauses.append("stretch_id = ?")
        params.append(stretch_id)
    if jurisdiction is not None:
        clauses.append("jurisdiction = ?")
        params.append(jurisdiction)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute_returning_dicts(
        f"SELECT * FROM stretch_anchors {where} ORDER BY stretch_id, anchor_type, anchor_id",
        params,
    )
    return [_row_to_anchor(r) for r in rows]


def list_segment_lines(db: Database, stretch_id: str) -> list[StretchSegmentLine]:
    """A stretch's segment lines in trace order (stretch_segments.seq)."""
    rows = db.execute_returning_dicts(
        "SELECT l.*, s.seq FROM stretch_segment_lines l "
        "JOIN stretch_segments s ON s.ogf_id = l.ogf_id "
        "WHERE l.stretch_id = ? ORDER BY s.seq",
        [stretch_id],
    )
    return [
        StretchSegmentLine(
            ogf_id=r["ogf_id"],
            stretch_id=r["stretch_id"],
            jurisdiction=r["jurisdiction"],
            name=r["name"],
            geometry=json.loads(r["geometry_geojson"]),
        )
        for r in rows
    ]


def _anchor_row(a: StretchAnchor) -> dict:
    return {
        "anchor_id": a.anchor_id,
        "stretch_id": a.stretch_id,
        "jurisdiction": a.jurisdiction,
        "anchor_type": a.anchor_type,
        "subtype": a.subtype,
        "name": a.name,
        "lat": a.lat,
        "lng": a.lon,
        "source": a.source,
        "source_ref": a.source_ref,
        "ogf_id": a.ogf_id,
        "snap_distance_m": a.snap_distance_m,
        "attributes_json": json.dumps(a.attributes, separators=(",", ":"), sort_keys=True),
        "built_at": a.built_at,
    }


def _line_row(line: StretchSegmentLine) -> dict:
    return {
        "ogf_id": line.ogf_id,
        "stretch_id": line.stretch_id,
        "jurisdiction": line.jurisdiction,
        "name": line.name,
        "geometry_geojson": json.dumps(line.geometry, separators=(",", ":")),
    }


def _row_to_anchor(r: dict) -> StretchAnchor:
    return StretchAnchor(
        anchor_id=r["anchor_id"],
        stretch_id=r["stretch_id"],
        jurisdiction=r["jurisdiction"],
        anchor_type=r["anchor_type"],
        subtype=r["subtype"],
        name=r["name"],
        lat=r["lat"],
        lon=r["lng"],
        source=r["source"],
        source_ref=r["source_ref"],
        ogf_id=r["ogf_id"],
        snap_distance_m=r["snap_distance_m"],
        attributes=json.loads(r["attributes_json"]),
        built_at=r["built_at"],
    )
