"""The Today view: the user's own pins, in the order they choose, each with a reason.

The user picks one key: conditions match, distance, last visited, or
favourites first. Every row says in words why it sits where it does, so a
ranking is never a bare order.

Only the user's own pins are listed, and only their own logged trips feed a
key. A recalled visit is not a logged trip: a pin with only recalled history
has no last visit here. A shared pin (phase 4) will be drawn on the map and is
not a row in this view, so it cannot change the order of the user's own.

The match key looks up today's conditions, which is a live call per pin that
has enough rated trips to be matched. The other keys make no outside call, so
only `match` rows carry the match; the pin's own `GET /pins/{id}/match` is the
way to read it for one pin under another sort.

Catch data stays queryable by species (idx_catches_user_species), so a species
filter can be added to the pin facts later without a schema change. It is not
built here.
"""

from datetime import date

from sqlite_utils import Database

from src.models.pin import PinMatch, TodayResponse, TodayRow
from src.services.pins import _state, labels
from src.services.pins.match import match_for_pin
from src.storage import pins as store

# How the match key orders its statuses, best first. A hidden match has no
# information, so it sits below every pin that was compared.
_MATCH_ORDER = {
    "similar": 0,
    "partly": 1,
    "different": 2,
    "not_enough_readings": 3,
    "few_good_trips": 3,
    "hidden": 4,
}


def _distance_text(km: float) -> str:
    if km < 1:
        return f"{round(km * 1000 / 10) * 10} m"
    return f"{km:.1f} km"


def _when(last_visit: str | None, today: date) -> tuple[str, int] | None:
    """How long ago a visit was, in words, and in days."""
    if not last_visit:
        return None
    try:
        days = max(0, (today - date.fromisoformat(last_visit[:10])).days)
    except ValueError:
        return None
    if days == 0:
        return "today", 0
    if days == 1:
        return "yesterday", 1
    return f"{days} days ago ({last_visit[:10]})", days


def _row(pin: dict, distance_km: float | None) -> dict:
    return {
        "pin_id": pin["id"],
        "name": pin["name"],
        "lat": pin["lat"],
        "lng": pin["lng"],
        "jurisdiction": pin["jurisdiction"],
        "is_favourite": bool(pin["is_favourite"]),
        "state": _state(pin["visits"], pin["recalled_count"]),
        "visits": pin["visits"],
        "last_visit": pin["last_visit"],
        "distance_km": distance_km,
        "match": None,
    }


def _by_name(row: dict) -> tuple[str, int]:
    return row["name"].lower(), row["pin_id"]


def today_view(
    db: Database,
    user_id: int,
    sort: str,
    lat: float | None = None,
    lng: float | None = None,
    today: date | None = None,
) -> dict:
    """GET /pins/today.

    `lat` and `lng` are where the user is. They are required for the distance
    key and optional otherwise, where they only add `distance_km` to each row.
    """
    if sort not in labels.TODAY_SORT_LABELS:
        raise ValueError(f"unknown sort {sort!r}")
    if (lat is None) != (lng is None):
        raise ValueError("lat and lng go together")
    if sort == "distance" and lat is None:
        raise ValueError("sorting by distance needs lat and lng")
    today = today or date.today()

    pins = store.list_pins(db, user_id)
    rows = []
    for pin in pins:
        km = (
            round(store.distance_m(lat, lng, pin["lat"], pin["lng"]) / 1000, 2)
            if lat is not None and lng is not None
            else None
        )
        rows.append(_row(pin, km))

    if sort == "match":
        ordered = _by_match(db, user_id, pins, rows)
    elif sort == "distance":
        ordered = sorted(rows, key=lambda r: (r["distance_km"], *_by_name(r)))
        for row in ordered:
            row["reason"] = labels.TODAY_DISTANCE_REASON.format(
                distance=_distance_text(row["distance_km"])
            )
    elif sort == "last_visited":
        ordered = _by_last_visit(rows)
        for row in ordered:
            row["reason"] = _last_visit_reason(row, today)
    else:
        # Favourites first; within each group the most recently fished first.
        ordered = sorted(
            _by_last_visit(rows), key=lambda r: not r["is_favourite"]
        )  # sorted() is stable, so the last-visit order holds inside each group
        for row in ordered:
            row["reason"] = (
                labels.TODAY_FAVOURITE_REASON
                if row["is_favourite"]
                else labels.TODAY_NOT_FAVOURITE_REASON
            )

    return TodayResponse(
        sort=sort,
        sort_label=labels.TODAY_SORT_LABELS[sort],
        rows=[TodayRow(rank=i, **row) for i, row in enumerate(ordered, start=1)],
    ).model_dump()


def _by_last_visit(rows: list[dict]) -> list[dict]:
    """Newest visit first, then pins never fished. Equal dates fall back to name."""
    by_name = sorted(rows, key=_by_name)
    visited = sorted(
        (r for r in by_name if r["last_visit"]), key=lambda r: r["last_visit"], reverse=True
    )
    return visited + [r for r in by_name if not r["last_visit"]]  # reverse=True keeps ties stable


def _last_visit_reason(row: dict, today: date) -> str:
    said = _when(row["last_visit"], today)
    if said is None:
        return labels.TODAY_NEVER_VISITED_REASON
    return labels.TODAY_LAST_VISITED_REASON.format(when=said[0])


def _by_match(db: Database, user_id: int, pins: list[dict], rows: list[dict]) -> list[dict]:
    by_id = {p["id"]: p for p in pins}
    matches: dict[int, PinMatch] = {}
    for row in rows:
        match = match_for_pin(db, user_id, by_id[row["pin_id"]])
        matches[row["pin_id"]] = match
        row["match"] = match
        row["reason"] = match.headline

    def key(row: dict) -> tuple:
        match = matches[row["pin_id"]]
        return (
            _MATCH_ORDER[match.status],
            -(match.score or 0.0),
            -match.compared,
            *_by_name(row),
        )

    return sorted(rows, key=key)
