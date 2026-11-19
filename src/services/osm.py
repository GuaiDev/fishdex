"""OSM service — bridges the Overpass ingest layer to the agent.

Uses importlib because src/ingest/global/ contains a Python keyword ('global')
that blocks normal import syntax.
"""

import importlib
import json
import logging
import math
import warnings
from datetime import date, datetime, timedelta

import httpx

from src.storage.database import get_db
from src.storage.osm_features import upsert_access_points, upsert_water_features

_osm = importlib.import_module("src.ingest.global.osm")
_log = logging.getLogger(__name__)

_OSM_CACHE_DAYS = 30
_OSM_WATER_RADIUS_KM = 50.0
_OSM_DEG_PER_KM = 1.0 / 111.0

_NON_FISHABLE_TYPES = frozenset({"ditch", "drain"})
_MIN_FISHABLE_AREA_M2 = 50.0
_MAX_RESULTS = 15
_ISOLATED_TYPES = frozenset({"pond", "reservoir"})
_DISPERSAL_NOTE = (
    "Isolated water body with recent waterfowl activity. Cyprinid colonization via "
    "bird dispersal is scientifically plausible (PNAS 2020) — worth sampling even "
    "without stream connectivity."
)
_DATA_STATUS = (
    "OSM shows what water exists and where. "
    "Current ranking is by convenience only (distance + size). "
    "Habitat suitability and species predictions are not yet available — "
    "Phase 2 will replace this ranking with a model-based untapped potential score."
)


def get_nearby_water_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 25,
    feature_type: str | None = None,
    not_in_trip_log: bool = False,
) -> str:
    """Return JSON with fishable water bodies near a location, ranked by convenience."""
    features = _osm.fetch_water_features(lat, lng, radius_km)

    if feature_type:
        features = [f for f in features if f.feature_type == feature_type]

    # Drop ditches, drains, and polygon features too small to fish
    features = [
        f
        for f in features
        if f.feature_type not in _NON_FISHABLE_TYPES
        and not (f.area_m2 is not None and f.area_m2 < _MIN_FISHABLE_AREA_M2)
    ]

    db = get_db()

    if not_in_trip_log:
        visited: set[str] = set()
        for row in db["trips"].rows_where("location_name IS NOT NULL"):
            name = row.get("location_name")
            if name:
                visited.add(name.lower())
        # Bidirectional substring: exclude if the water body name appears in any trip
        # location, or any trip location appears in the water body name.
        features = [
            f
            for f in features
            if not f.name or not any(f.name.lower() in v or v in f.name.lower() for v in visited)
        ]

    if not features:
        return json.dumps(
            {
                "water_bodies": [],
                "data_status": _DATA_STATUS,
                "note": f"No fishable water features found within {radius_km:.0f}km.",
            }
        )

    # Build result dicts with raw values for scoring
    results = []
    for f in features:
        dist = _haversine_km(lat, lng, f.lat, f.lng)
        if f.name:
            display_name = f.name
        elif f.area_m2 is not None:
            area_ha = f.area_m2 / 10_000
            display_name = f"unnamed {f.feature_type} (~{area_ha:.1f} ha)"
        else:
            display_name = f"unnamed {f.feature_type}"

        entry: dict = {
            "osm_id": f.osm_id,
            "display_name": display_name,
            "name": f.name,
            "feature_type": f.feature_type,
            "distance_km": round(dist, 2),
            "jurisdiction": f.jurisdiction,
            "area_m2": f.area_m2,
            "_dist": dist,
            "_area": f.area_m2 or 0.0,
        }
        if (
            f.feature_type in _ISOLATED_TYPES
            and not _has_nearby_stream(db, f.lat, f.lng)
            and _has_recent_bird_activity(db, f.lat, f.lng)
        ):
            entry["waterfowl_dispersal_flag"] = True
            entry["dispersal_note"] = _DISPERSAL_NOTE
        results.append(entry)

    # Composite convenience score: 60% distance (closer = better) +
    # 40% inverted size (larger = more water to explore, NOT a quality signal).
    max_dist = max(r["_dist"] for r in results) or 1.0
    max_area = max(r["_area"] for r in results) or 1.0
    for r in results:
        dist_score = r["_dist"] / max_dist
        size_score = r["_area"] / max_area
        r["_score"] = 0.6 * dist_score + 0.4 * (1.0 - size_score)

    results.sort(key=lambda x: x["_score"])
    results = results[:_MAX_RESULTS]

    for r in results:
        del r["_dist"]
        del r["_area"]
        del r["_score"]

    return json.dumps({"water_bodies": results, "data_status": _DATA_STATUS})


def get_access_points_for_agent(
    lat: float,
    lng: float,
    radius_km: float = 25,
    access_type: str | None = None,
) -> str:
    """Return JSON with nearby access points."""
    points = _osm.fetch_access_points(lat, lng, radius_km)

    if access_type:
        points = [p for p in points if p.access_type == access_type]

    if not points:
        return json.dumps(
            {
                "access_points": [],
                "note": f"No access points found within {radius_km:.0f}km.",
            }
        )

    results = []
    for p in points:
        dist = _haversine_km(lat, lng, p.lat, p.lng)
        results.append(
            {
                "osm_id": p.osm_id,
                "name": p.name or f"unnamed {p.access_type.replace('_', ' ')}",
                "access_type": p.access_type,
                "distance_km": round(dist, 2),
                "jurisdiction": p.jurisdiction,
            }
        )

    results.sort(key=lambda x: x["distance_km"])
    return json.dumps({"access_points": results[:_MAX_RESULTS]})


def _has_nearby_stream(db, lat: float, lng: float, radius_m: float = 200) -> bool:
    """Return True if any OHN stream segment has a first vertex within radius_m."""
    if "stream_segments" not in db.table_names():
        return False
    deg = radius_m / 111_000
    row = db.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT
            CAST(SUBSTR(p, 1, INSTR(p,' ')-1) AS REAL) vlng,
            CAST(SUBSTR(p, INSTR(p,' ')+1,
                        CASE WHEN INSTR(p,',')>0
                             THEN INSTR(p,',')-INSTR(p,' ')-1
                             ELSE LENGTH(p) END) AS REAL) vlat
          FROM (SELECT TRIM(SUBSTR(geom_wkt, INSTR(geom_wkt,'(')+1)) p
                FROM stream_segments)
        )
        WHERE vlat BETWEEN ? AND ? AND vlng BETWEEN ? AND ?
        """,
        [lat - deg, lat + deg, lng - deg * 1.4, lng + deg * 1.4],
    ).fetchone()
    return bool(row and row[0] > 0)


def _has_recent_bird_activity(
    db, lat: float, lng: float, days: int = 30, radius_m: float = 500
) -> bool:
    """Return True if bird_observations exist within radius_m in the last days."""
    if "bird_observations" not in db.table_names():
        return False
    deg = radius_m / 111_000
    since = (date.today() - timedelta(days=days)).isoformat()
    row = db.execute(
        """SELECT COUNT(*) FROM bird_observations
           WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND observed_on >= ?""",
        [lat - deg, lat + deg, lng - deg * 1.4, lng + deg * 1.4, since],
    ).fetchone()
    return bool(row and row[0] > 0)


def fetch_and_store(lat: float, lng: float) -> tuple[int, int]:
    """Fetch and upsert OSM features. Called by the ingest CLI.

    Skips the Overpass fetch entirely if fresh data (< 30 days old) already
    exists within the fetch radius — prevents redundant calls and the concurrent
    memory pressure that can exhaust the host when multiple ingests overlap.
    Returns (0, 0) if Overpass is unavailable.
    """
    from src.services.ingest_runs import record_counts, record_error

    db = get_db()

    if "water_features" in db.table_names():
        deg = _OSM_WATER_RADIUS_KM * _OSM_DEG_PER_KM
        cutoff = (datetime.utcnow() - timedelta(days=_OSM_CACHE_DAYS)).isoformat()
        cached = db.execute(
            """SELECT COUNT(*) FROM water_features
               WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?
               AND fetched_at >= ?""",
            [lat - deg, lat + deg, lng - deg, lng + deg, cutoff],
        ).fetchone()[0]
        if cached > 0:
            water_count = db.execute(
                """SELECT COUNT(*) FROM water_features
                   WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
                [lat - deg, lat + deg, lng - deg, lng + deg],
            ).fetchone()[0]
            access_count = (
                db.execute(
                    """SELECT COUNT(*) FROM access_points
                       WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
                    [lat - deg, lat + deg, lng - deg, lng + deg],
                ).fetchone()[0]
                if "access_points" in db.table_names()
                else 0
            )
            _log.info(
                "OSM: skipping fetch — %d cached features within %.0fkm "
                "(last fetched < %d days ago)",
                cached,
                _OSM_WATER_RADIUS_KM,
                _OSM_CACHE_DAYS,
            )
            record_counts(skipped=water_count + access_count)
            return water_count, access_count

    try:
        features = _osm.fetch_water_features(lat, lng, radius_km=50)
        record_counts(fetched=1, empty=int(not features))
        points = _osm.fetch_access_points(lat, lng, radius_km=25)
        record_counts(fetched=1, empty=int(not points))
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
        record_error(e)
        warnings.warn(f"OSM ingest failed after all retries — skipping: {e}", stacklevel=2)
        return 0, 0

    upsert_water_features(db, features)
    upsert_access_points(db, points)

    record_counts(stored=len(features) + len(points))
    return len(features), len(points)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
