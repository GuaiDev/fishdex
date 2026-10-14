"""Water body storage: the rivers, creeks and lakes spots hang off.

Rebuilt wholesale with the spots, in the same transaction (see
storage/spots.py::replace_spots), so a map never reads spots that point at a
water body the rebuild removed.
"""

import json
import sqlite3

from sqlite_utils.db import Database

from src.models.bathymetry import BathymetryContour
from src.models.shoreline_feature import ShorelineFeature
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


def write_shoreline_features(
    conn: sqlite3.Connection, jurisdiction: str, features: list[ShorelineFeature]
) -> None:
    """Swap in a jurisdiction's shoreline features. The caller commits."""
    conn.execute("DELETE FROM shoreline_features WHERE jurisdiction = ?", [jurisdiction])
    rows = [_feature_row(f) for f in features]
    if rows:
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO shoreline_features ({', '.join(cols)}) "
            f"VALUES ({', '.join(':' + c for c in cols)})",
            rows,
        )


def write_bathymetry_contours(
    conn: sqlite3.Connection, jurisdiction: str, contours: list[BathymetryContour]
) -> None:
    """Swap in a jurisdiction's raw bathymetry contours. The caller commits.

    The depth lines behind the drop-off and hump features, so a rebuild replaces
    them together in one transaction (storage/spots.py::replace_spots).
    """
    conn.execute("DELETE FROM bathymetry_contours WHERE jurisdiction = ?", [jurisdiction])
    rows = [_contour_row(c) for c in contours]
    if rows:
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO bathymetry_contours ({', '.join(cols)}) "
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


def count_shoreline_features(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM shoreline_features").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM shoreline_features WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_shoreline_features(
    db: Database, water_body_id: str, kinds: list[str] | None = None
) -> list[ShorelineFeature]:
    """A water body's shoreline features, in id order. Optionally one or more kinds."""
    clauses = ["water_body_id = ?"]
    params: list = [water_body_id]
    if kinds:
        clauses.append(f"kind IN ({', '.join('?' * len(kinds))})")
        params += kinds
    rows = db.execute_returning_dicts(
        f"SELECT * FROM shoreline_features WHERE {' AND '.join(clauses)} ORDER BY feature_id",
        params,
    )
    return [_feature_from_row(r) for r in rows]


def count_bathymetry_contours(db: Database, jurisdiction: str | None = None) -> int:
    if jurisdiction is None:
        return db.execute("SELECT COUNT(*) FROM bathymetry_contours").fetchone()[0]
    return db.execute(
        "SELECT COUNT(*) FROM bathymetry_contours WHERE jurisdiction = ?", [jurisdiction]
    ).fetchone()[0]


def list_bathymetry_contours(
    db: Database, water_body_id: str | None = None
) -> list[BathymetryContour]:
    """Raw depth contours, all of them or one water body's, in depth then id order."""
    if water_body_id is None:
        rows = db.execute_returning_dicts(
            "SELECT * FROM bathymetry_contours ORDER BY depth_m, ogf_id"
        )
    else:
        rows = db.execute_returning_dicts(
            "SELECT * FROM bathymetry_contours WHERE water_body_id = ? ORDER BY depth_m, ogf_id",
            [water_body_id],
        )
    return [_contour_from_row(r) for r in rows]


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


def _feature_row(f: ShorelineFeature) -> dict:
    return {
        "feature_id": f.feature_id,
        "water_body_id": f.water_body_id,
        "jurisdiction": f.jurisdiction,
        "kind": f.kind,
        "name": f.name,
        "geometry_geojson": _dumps(f.geometry),
        "source": f.source,
        "source_date": f.source_date,
        "provenance": f.provenance,
        "derivation": f.derivation,
        "note": f.note,
        "attributes_json": _dumps(f.attributes),
        "area_m2": f.area_m2,
        "length_m": f.length_m,
        "built_at": f.built_at,
    }


def _feature_from_row(r: dict) -> ShorelineFeature:
    return ShorelineFeature(
        feature_id=r["feature_id"],
        water_body_id=r["water_body_id"],
        jurisdiction=r["jurisdiction"],
        kind=r["kind"],
        name=r["name"],
        geometry=json.loads(r["geometry_geojson"]),
        source=r["source"],
        source_date=r["source_date"],
        provenance=r["provenance"],
        derivation=r["derivation"],
        note=r["note"],
        attributes=json.loads(r["attributes_json"]),
        area_m2=r["area_m2"],
        length_m=r["length_m"],
        built_at=r["built_at"],
    )


def _contour_row(c: BathymetryContour) -> dict:
    return {
        "ogf_id": c.ogf_id,
        "water_body_id": c.water_body_id,
        "jurisdiction": c.jurisdiction,
        "depth_m": c.depth_m,
        "survey_method": c.survey_method,
        "survey_date": c.survey_date,
        "creation_methodology": c.creation_methodology,
        "location_accuracy": c.location_accuracy,
        "geometry_geojson": _dumps(c.geometry),
        "source": c.source,
        "licence": c.licence,
        "built_at": c.built_at,
    }


def _contour_from_row(r: dict) -> BathymetryContour:
    return BathymetryContour(
        ogf_id=r["ogf_id"],
        water_body_id=r["water_body_id"],
        jurisdiction=r["jurisdiction"],
        depth_m=r["depth_m"],
        survey_method=r["survey_method"],
        survey_date=r["survey_date"],
        creation_methodology=r["creation_methodology"],
        location_accuracy=r["location_accuracy"],
        geometry=json.loads(r["geometry_geojson"]),
        source=r["source"],
        licence=r["licence"],
        built_at=r["built_at"],
    )
