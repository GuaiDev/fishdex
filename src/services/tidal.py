"""Agent tool: tidal conditions from CHS (Canadian Hydrographic Service).

Wraps the CHS tidal adapter to provide nearest-station high/low tide times
for use by the chat agent. Relevant for BC coast, NS, NB, PEI, and NL.
"""

import logging
import math
from datetime import UTC, datetime

from src.storage.database import get_db

logger = logging.getLogger(__name__)

_INGEST_HINT = "fishbot ingest-source CA-NS tidal --area 'Annapolis River NS'"


def get_tidal_conditions_for_agent(lat: float, lng: float) -> dict:
    """Return the nearest tidal station's next high and low tide predictions.

    Queries the tidal_readings table (populated by `fishbot ingest-source <jurisdiction> tidal`).
    Returns a dict with next_high, next_low, station info, or a message
    if no tidal data is available for this location.
    """
    db = get_db()

    if "tidal_readings" not in db.table_names():
        return {
            "available": False,
            "message": f"No tidal data ingested yet. Run `{_INGEST_HINT}` for coastal locations.",
        }

    # Find the nearest station with predictions within 200km
    rows = list(
        db.execute("""
        SELECT DISTINCT station_id, station_name, lat, lng
        FROM tidal_readings
        WHERE lat IS NOT NULL AND lng IS NOT NULL
    """).fetchall()
    )

    if not rows:
        return {
            "available": False,
            "message": (
                f"No tidal stations in database. Run `{_INGEST_HINT}` for coastal locations."
            ),
        }

    # Pick nearest station
    nearest = min(rows, key=lambda r: _haversine_km(lat, lng, r[2], r[3]))
    station_id, station_name, slat, slng = nearest
    dist_km = _haversine_km(lat, lng, slat, slng)

    if dist_km > 200:
        return {
            "available": False,
            "message": (
                f"No tidal station within 200km. Nearest is {station_name} "
                f"({dist_km:.0f}km away). This location may not be tidal."
            ),
        }

    now_iso = datetime.now(tz=UTC).isoformat()

    # Get next high and next low
    future_preds = list(
        db.execute(
            """
        SELECT tide_type, prediction_datetime, water_level_m
        FROM tidal_readings
        WHERE station_id = ?
          AND prediction_datetime > ?
          AND tide_type IN ('high', 'low')
        ORDER BY prediction_datetime ASC
        LIMIT 10
    """,
            [station_id, now_iso[:19]],
        ).fetchall()
    )

    next_high = next((r for r in future_preds if r[0] == "high"), None)
    next_low = next((r for r in future_preds if r[0] == "low"), None)

    return {
        "available": True,
        "station_name": station_name,
        "station_distance_km": round(dist_km, 1),
        "next_high_tide": {
            "datetime": next_high[1] if next_high else None,
            "water_level_m": next_high[2] if next_high else None,
        },
        "next_low_tide": {
            "datetime": next_low[1] if next_low else None,
            "water_level_m": next_low[2] if next_low else None,
        },
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))
