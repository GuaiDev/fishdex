"""Trip CRUD via sqlite-utils.

Trips serialize to a single row each. JSON columns (species_caught, conditions,
gear_used) are stored as JSON-encoded strings and round-tripped through Pydantic.

Parsed trip functions (insert_parsed_trip, get_parsed_trips, etc.) operate on the
`parsed_trips` table populated by the natural-language trip logger.
"""

import json
import math
from datetime import datetime
from typing import Any

from sqlite_utils.db import Database

from src.models.catch import Catch
from src.models.trip import Trip


def insert_trip(db: Database, trip: Trip, *, user_id: int) -> int:
    row = _trip_to_row(trip)
    row.pop("id", None)
    row["user_id"] = user_id
    return db["trips"].insert(row).last_pk


def get_trip(db: Database, trip_id: int, *, user_id: int) -> Trip | None:
    rows = list(db["trips"].rows_where("id = ? AND user_id = ?", [trip_id, user_id]))
    if not rows:
        return None
    return _row_to_trip(rows[0])


def update_trip(db: Database, trip_id: int, *, user_id: int, **fields: Any) -> None:
    """Update a trip the user owns. Someone else's trip id is a no-op, not an edit."""
    encoded: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"species_caught", "conditions", "gear_used"}:
            encoded[key] = json.dumps(_to_jsonable(value))
        else:
            encoded[key] = value
    encoded["updated_at"] = datetime.now().isoformat()
    if get_trip(db, trip_id, user_id=user_id) is None:
        return
    db["trips"].update(trip_id, encoded)


def recent_trips(
    db: Database, *, user_id: int, limit: int = 5, status: str = "completed"
) -> list[Trip]:
    rows = db["trips"].rows_where(
        "status = ? AND user_id = ?", [status, user_id], order_by="date desc", limit=limit
    )
    return [_row_to_trip(r) for r in rows]


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    return value


def _trip_to_row(trip: Trip) -> dict[str, Any]:
    row = trip.model_dump(mode="json")
    row["species_caught"] = json.dumps([c.model_dump(mode="json") for c in trip.species_caught])
    row["conditions"] = json.dumps(trip.conditions)
    row["gear_used"] = json.dumps(trip.gear_used)
    return row


def _row_to_trip(row: dict[str, Any]) -> Trip:
    decoded = dict(row)
    decoded["species_caught"] = [
        Catch.model_validate(c) for c in json.loads(row.get("species_caught") or "[]")
    ]
    decoded["conditions"] = json.loads(row.get("conditions") or "{}")
    decoded["gear_used"] = json.loads(row.get("gear_used") or "[]")
    return Trip.model_validate(decoded)


# ── parsed_trips (NL trip logger) ─────────────────────────────────────────────


def insert_parsed_trip(db: Any, trip_dict: dict, *, user_id: int) -> int:
    """Insert a parsed trip into parsed_trips. Returns trip_id."""
    row: dict[str, Any] = {
        "user_id": str(user_id),
        "logged_at": datetime.now().isoformat(),
        "trip_date": trip_dict.get("date"),
        "location_description": trip_dict.get("location_description", ""),
        "waterbody_name": trip_dict.get("waterbody_name"),
        "lat": trip_dict.get("lat"),
        "lng": trip_dict.get("lng"),
        "ogf_id": trip_dict.get("ogf_id"),
        "distance_to_segment_m": trip_dict.get("distance_to_segment_m"),
        "species_caught": json.dumps(trip_dict.get("species_caught") or []),
        "species_observed": json.dumps(trip_dict.get("species_observed") or []),
        "species_targeted": trip_dict.get("species_targeted"),
        "water_level": (trip_dict.get("conditions") or {}).get("water_level"),
        "water_clarity": (trip_dict.get("conditions") or {}).get("water_clarity"),
        "water_temp_c": (trip_dict.get("conditions") or {}).get("water_temp_c"),
        "weather": (trip_dict.get("conditions") or {}).get("weather"),
        "flow_trend": (trip_dict.get("conditions") or {}).get("flow_trend"),
        "habitat_notes": trip_dict.get("habitat_notes"),
        "spot_type": trip_dict.get("spot_type"),
        "fish_count": trip_dict.get("fish_count"),
        "was_productive": (
            int(trip_dict["was_productive"])
            if trip_dict.get("was_productive") is not None
            else None
        ),
        "gear": trip_dict.get("gear"),
        "notes": trip_dict.get("notes"),
        "raw_text": trip_dict.get("raw_text", ""),
        "location_method": trip_dict.get("location_method"),
        "location_confidence": trip_dict.get("location_confidence"),
    }
    return db["parsed_trips"].insert(row).last_pk  # type: ignore[return-value]


def get_parsed_trips(
    db: Any,
    *,
    user_id: int,
    limit: int = 50,
    species: str | None = None,
    ogf_id: int | None = None,
) -> list[dict]:
    """Return parsed trips, most recent first."""
    where_clauses = ["user_id = ?"]
    params: list[Any] = [str(user_id)]

    if species:
        where_clauses.append("(species_caught LIKE ? OR species_observed LIKE ?)")
        params.extend([f"%{species}%", f"%{species}%"])
    if ogf_id is not None:
        where_clauses.append("ogf_id = ?")
        params.append(ogf_id)

    where = " AND ".join(where_clauses)
    rows = db["parsed_trips"].rows_where(where, params, order_by="logged_at desc", limit=limit)
    return [_decode_parsed_trip(r) for r in rows]


def get_parsed_trips_near(
    db: Any, lat: float, lng: float, *, user_id: int, radius_km: float = 25.0
) -> list[dict]:
    """Return the user's parsed trips within radius_km of the given coordinates."""
    deg = radius_km / 111.0
    rows = db["parsed_trips"].rows_where(
        "user_id = ? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
        [str(user_id), lat - deg, lat + deg, lng - deg * 1.4, lng + deg * 1.4],
        order_by="logged_at desc",
    )
    results = []
    for row in rows:
        trip = _decode_parsed_trip(row)
        if trip.get("lat") and trip.get("lng"):
            dist = _haversine_km(lat, lng, trip["lat"], trip["lng"])
            if dist <= radius_km:
                trip["distance_from_query_km"] = round(dist, 2)
                results.append(trip)
    return results


def get_species_parsed_trips(db: Any, species: str, *, user_id: int) -> list[dict]:
    """Return the user's trips where the species was caught or observed."""
    return get_parsed_trips(db, user_id=user_id, limit=500, species=species)


def get_productive_segments(db: Any, *, user_id: int) -> list[int]:
    """Return ogf_ids of segments with at least one productive=True trip by the user."""
    rows = db["parsed_trips"].rows_where(
        "user_id = ? AND was_productive = 1 AND ogf_id IS NOT NULL", [str(user_id)]
    )
    return list({int(r["ogf_id"]) for r in rows})


def _decode_parsed_trip(row: dict) -> dict:
    decoded = dict(row)
    decoded["species_caught"] = json.loads(row.get("species_caught") or "[]")
    decoded["species_observed"] = json.loads(row.get("species_observed") or "[]")
    if decoded.get("was_productive") is not None:
        decoded["was_productive"] = bool(decoded["was_productive"])
    return decoded


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))
