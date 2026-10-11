"""Personal-map pins: linking trips to the user's places, and the facts shown
on each place.

Linking is per stop, because a trip can cover several places and the stops
carry the coordinates. A stop links to a pin without being asked only when
exactly one of the user's pins is within LINK_RADIUS_M; that link is
"inferred" and can be undone in one tap. With two or more pins in range the
user is shown the candidates; a link they choose is "confirmed".

Nothing here reaches a model. The facts are counts and maxima over the
user's own logged trips. Recalled records are listed beside them, marked as
recalled, and never counted in them.
"""

import json
from datetime import datetime

from sqlite_utils import Database

from src.services.pins.labels import LINK_RADIUS_M
from src.storage import pins as store

UNIDENTIFIED = "unidentified sp."


def _candidate(pin: dict) -> dict:
    return {"pin_id": pin["id"], "name": pin["name"], "distance_m": pin["distance_m"]}


def _nearby_unpinned_count(
    db: Database, user_id: int, session_id: int, lat: float, lng: float
) -> int:
    """Earlier located stops from other trips, not linked to any pin, within the radius."""
    return sum(
        1
        for stop in store.unpinned_stops(db, user_id)
        if stop["session_id"] != session_id
        and store.distance_m(lat, lng, *store.stop_point(stop)) <= LINK_RADIUS_M
    )


def link_session_stops(db: Database, user_id: int, session_id: int) -> list[dict]:
    """Link a newly logged trip's stops to the user's pins, and say what happened.

    One entry per stop:
      link        the inferred link made, or None
      candidates  pins in range when there were two or more (the user picks)
      pin_prompt  True when no pin is in range but an earlier unpinned stop
                  of the user's is: the second visit is when "Pin this spot"
                  is offered, not the first.
    """
    results = []
    for stop in store.session_stops(db, user_id, session_id):
        point = store.stop_point(stop)
        entry = {
            "stop_id": stop["id"],
            "location_name": stop["location_name"] or stop["location_text"] or None,
            "lat": point[0] if point else None,
            "lng": point[1] if point else None,
            "link": None,
            "candidates": [],
            "pin_prompt": False,
        }
        if point is None:
            results.append(entry)
            continue
        near = store.pins_within(db, user_id, point[0], point[1], LINK_RADIUS_M)
        if len(near) == 1:
            link = store.link_stop(db, user_id, near[0]["id"], stop["id"], "inferred")
            entry["link"] = {
                "pin_id": near[0]["id"],
                "name": near[0]["name"],
                "kind": link["link_kind"],
                "distance_m": link["distance_m"],
            }
        elif near:
            entry["candidates"] = [_candidate(p) for p in near]
        else:
            entry["pin_prompt"] = (
                _nearby_unpinned_count(db, user_id, session_id, point[0], point[1]) > 0
            )
        results.append(entry)
    return results


def create_pin(
    db: Database,
    user_id: int,
    name: str,
    lat: float,
    lng: float,
    notes: str | None = None,
    is_favourite: bool = False,
    from_stop_id: int | None = None,
) -> dict | None:
    """Create a pin and link what belongs to it.

    from_stop_id ("Pin this spot") links that stop as confirmed. Then every
    unpinned stop of the user's for which the new pin is the only pin in
    range links as inferred: the same rule a newly logged stop follows, so
    pinning a place you have fished before picks up those trips, each one
    undoable. Returns None when from_stop_id is not one of the user's stops.
    """
    if from_stop_id is not None and store.get_stop(db, user_id, from_stop_id) is None:
        return None
    pin_id = store.create_pin(db, user_id, name, lat, lng, notes, is_favourite)
    if from_stop_id is not None:
        store.link_stop(db, user_id, pin_id, from_stop_id, "confirmed")

    inferred = []
    for stop in store.unpinned_stops(db, user_id):
        point = store.stop_point(stop)
        near = store.pins_within(db, user_id, point[0], point[1], LINK_RADIUS_M)
        if len(near) == 1 and near[0]["id"] == pin_id:
            store.link_stop(db, user_id, pin_id, stop["id"], "inferred")
            inferred.append(stop["id"])
    return {"pin_id": pin_id, "inferred_stop_ids": inferred}


def _state(visits: int, recalled_count: int) -> str:
    # A recalled-only pin is Visited: the user has fished there. It is still
    # excluded from every statistic.
    return "visited" if visits or recalled_count else "saved"


def map_payload(db: Database, user_id: int) -> dict:
    """GET /pins: the Personal map's pins, and the stops not yet linked to one."""
    pins = store.list_pins(db, user_id)
    out_pins = [
        {
            "id": p["id"],
            "name": p["name"],
            "lat": p["lat"],
            "lng": p["lng"],
            "jurisdiction": p["jurisdiction"],
            "is_favourite": bool(p["is_favourite"]),
            "notes": p["notes"],
            "state": _state(p["visits"], p["recalled_count"]),
            "visits": p["visits"],
            "has_recalled": p["recalled_count"] > 0,
            "last_visit": p["last_visit"],
        }
        for p in pins
    ]
    stops = []
    for stop in store.unpinned_stops(db, user_id):
        lat, lng = store.stop_point(stop)
        near = [
            {"pin_id": p["id"], "name": p["name"], "distance_m": round(d, 1)}
            for p in pins
            if (d := store.distance_m(lat, lng, p["lat"], p["lng"])) <= LINK_RADIUS_M
        ]
        stops.append(
            {
                "stop_id": stop["id"],
                "session_id": stop["session_id"],
                "lat": lat,
                "lng": lng,
                "location": stop["location_name"] or stop["location_text"] or "Unknown",
                "date": stop["date"] or stop["date_approx"],
                "species": json.loads(stop["species_caught"] or "[]"),
                "productive": bool(stop["was_productive"]),
                "technique": stop["technique"],
                "gear": stop["gear"],
                "conditions": {
                    "air_temp_c": stop["air_temp_c"],
                    "pressure_hpa": stop["pressure_hpa"],
                    "anomaly_flag": stop["anomaly_flag"],
                }
                if stop["air_temp_c"] is not None
                else None,
                "candidates": sorted(near, key=lambda c: c["distance_m"]),
            }
        )
    return {"pins": out_pins, "unpinned_stops": stops}


def _hours(started_at: str | None, ended_at: str | None) -> float | None:
    if not started_at or not ended_at:
        return None
    try:
        span = datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
    except (TypeError, ValueError):  # unparseable, or one aware and one naive
        return None
    hours = span.total_seconds() / 3600
    return hours if hours > 0 else None


def pin_detail(db: Database, user_id: int, pin_id: int) -> dict | None:
    """GET /pins/{id}: the pin, its facts, its trips and its recalled records.

    Facts come only from logged trips linked to the pin:
      visits         distinct trips
      fish_logged    sum of catch counts, a missing count read as one fish
      species        confirmed species, excluding "unidentified sp."
      biggest_fish   the largest sized catch
      fish_per_hour  over timed trips spent entirely at this pin; a trip that
                     also stopped elsewhere has hours that cannot be split
      last_visit     newest dated trip
    Ratings are listed per trip and never averaged.
    """
    pin = store.get_pin(db, user_id, pin_id)
    if pin is None:
        return None
    link_rows = store.pin_link_rows(db, user_id, pin_id)
    catches = store.pin_catches(db, user_id, pin_id)
    recalled = store.list_recalled_visits(db, user_id, pin_id)

    trips: dict[int, dict] = {}
    for row in link_rows:
        trip = trips.setdefault(
            row["session_id"],
            {
                "session_id": row["session_id"],
                "date": row["date"],
                "date_approx": row["date_approx"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "rating": None,
                "fish": 0,
                "stops": [],
            },
        )
        if row["rating"] is not None:
            trip["rating"] = row["rating"]
        trip["stops"].append(
            {
                "stop_id": row["stop_id"],
                "kind": row["link_kind"],
                "distance_m": row["distance_m"],
                "location": row["location_name"] or row["location_text"] or None,
            }
        )

    species: dict[str, str] = {}
    biggest = None
    for c in catches:
        if c["session_id"] in trips:
            trips[c["session_id"]]["fish"] += c["count"] if c["count"] is not None else 1
        name = (c["species"] or "").strip()
        if c["species_confirmed"] and name and name.lower() != UNIDENTIFIED:
            species.setdefault(name.lower(), name)
        if c["biggest_size_cm"] is not None and (
            biggest is None or c["biggest_size_cm"] > biggest["size_cm"]
        ):
            biggest = {
                "species": name or None,
                "size_cm": c["biggest_size_cm"],
                "species_confirmed": bool(c["species_confirmed"]),
            }

    stop_counts = store.session_stop_counts(db, user_id, list(trips))
    timed_hours = 0.0
    timed_fish = 0
    timed_trips = 0
    for trip in trips.values():
        hours = _hours(trip["started_at"], trip["ended_at"])
        if hours is None or len(trip["stops"]) != stop_counts.get(trip["session_id"]):
            continue
        timed_hours += hours
        timed_fish += trip["fish"]
        timed_trips += 1

    dated = [t["date"] for t in trips.values() if t["date"]]
    facts = {
        "visits": len(trips),
        "fish_logged": sum(t["fish"] for t in trips.values()),
        "species": sorted(species.values(), key=str.lower),
        "biggest_fish": biggest,
        "fish_per_hour": round(timed_fish / timed_hours, 2) if timed_hours else None,
        "fish_per_hour_trips": timed_trips,
        "last_visit": max(dated) if dated else None,
        "is_favourite": bool(pin["is_favourite"]),
    }
    return {
        "pin": {
            **pin,
            "is_favourite": bool(pin["is_favourite"]),
            "state": _state(len(trips), len(recalled)),
            "has_recalled": bool(recalled),
        },
        "facts": facts,
        "trips": list(trips.values()),
        "recalled": [{**r, "provenance": "recalled"} for r in recalled],
    }
