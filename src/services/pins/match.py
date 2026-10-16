"""The match indicator: does today look like the user's best days at this pin?

It is arithmetic over the user's own rated trips at the pin and nothing else.
No model, no other angler's data, no seasonal baseline. The steps:

  1. Take the trips the user rated at this pin. Fewer than
     MIN_RATED_TRIPS_FOR_MATCH and the match is hidden; the pin shows its
     conditions panel alone, and nothing live is looked up.
  2. Keep the best of them: the BEST_TRIP_COUNT highest-rated, ties included,
     and only those rated MIN_BEST_RATING or better.
  3. For each condition, take its range over those trips, using the reading
     stored with each trip (session_conditions, one row per trip, taken for
     the trip's own time).
  4. Compare today's reading with each range. A reading that is stale, or
     missing, or that has too little history to compare against, is listed as
     left out with the reason and never guessed at.

Today's readings come from the same context-layer call as the conditions panel
(`describe(..., caller="pin_panel")`), so what the match compares is what the
panel shows, with the same stale limits. Flow is compared only against trips
whose snapshot came from the gauge that reports today: a flow from another
station is another river, and the panel itself never calls a flow "normal".

Recalled visits live in their own table and are never read here. A pin is read
through its owner's user_id only; another user's pin is a missing one.
"""

from dataclasses import dataclass
from datetime import date

from sqlite_utils import Database

from src.models.freshness import WATER_QUALITY_STALE_S, parse_observed_at
from src.models.pin import (
    MatchComparison,
    MatchLeftOut,
    MatchPosition,
    PinMatch,
)
from src.services.context import describe
from src.services.pins import labels
from src.storage import pins as store

_FIELD_SPECS = {spec["field"]: spec for spec in labels.CONDITION_FIELDS}


@dataclass(frozen=True)
class Reading:
    """One of today's readings, reduced to what the match needs."""

    value: float | None
    stale: bool = False
    empty_reason: str | None = None
    station: str | None = None


# -- wording -----------------------------------------------------------------------


def _number(value: float) -> str:
    return f"{round(float(value), 3):g}"


def _with_unit(text: str, unit: str | None) -> str:
    """Degrees sit against the number ("10°C"); every other unit takes a space."""
    if not unit:
        return text
    return f"{text}{unit}" if unit == "°C" else f"{text} {unit}"


def _amount(value: float, unit: str | None) -> str:
    return _with_unit(_number(value), unit)


def _range(low: float, high: float, unit: str | None) -> str:
    if low == high:
        return _amount(low, unit)
    return _with_unit(f"{_number(low)}–{_number(high)}", unit)


def _month_list(months: list[str]) -> str:
    if len(months) <= 2:
        return " and ".join(months)
    return f"{', '.join(months[:-1])} and {months[-1]}"


def _trips_word(n: int) -> str:
    return "trip" if n == 1 else "trips"


def hidden_headline(rated: int) -> str:
    needed = labels.MIN_RATED_TRIPS_FOR_MATCH - rated
    return labels.MATCH_HIDDEN_HEADLINE.format(needed=needed, trips=_trips_word(needed))


# -- choosing the best trips -------------------------------------------------------


def best_trips(trips: list[dict]) -> list[dict]:
    """The best-rated trips: the top BEST_TRIP_COUNT, ties kept, none below MIN_BEST_RATING."""
    good = [t for t in trips if t["rating"] is not None and t["rating"] >= labels.MIN_BEST_RATING]
    if not good:
        return []
    ranked = sorted(t["rating"] for t in good)[::-1]
    cutoff = ranked[min(labels.BEST_TRIP_COUNT, len(ranked)) - 1]
    return [t for t in good if t["rating"] >= cutoff]


def _months(trips: list[dict]) -> list[str]:
    found = set()
    for trip in trips:
        try:
            found.add(date.fromisoformat(str(trip["date"])[:10]).month)
        except (TypeError, ValueError):
            continue
    return [labels.MONTH_NAMES[m - 1] for m in sorted(found)]


# -- reading the history -----------------------------------------------------------


def _sample_is_near_trip(trip: dict) -> bool:
    """A water sample describes the trip only when it was taken close to it."""
    sampled = parse_observed_at(trip.get("pwqmn_sampled_at"))
    taken = parse_observed_at(trip.get("date"))
    if sampled is None or taken is None:
        return False
    return abs((sampled - taken).total_seconds()) <= WATER_QUALITY_STALE_S


def _gauge_identity(trip: dict) -> str | None:
    """How the panel names a gauge: its station name, else its id."""
    return trip.get("gauge_station_name") or trip.get("gauge_station_id")


def _history(spec: dict, trips: list[dict], today: Reading) -> tuple[list[float], str | None]:
    """The best trips' values for one condition, and why some were unusable.

    Returns (values, reason) where reason is the left-out code to use if there
    are too few values: it says what was dropped, or None if nothing was.
    """
    recorded = [t for t in trips if t.get(spec["column"]) is not None]
    usable = recorded
    reason = None
    if spec["source"] == "gauge":
        usable = [t for t in recorded if _gauge_identity(t) == today.station]
        reason = "other_gauge" if len(usable) < len(recorded) else None
    elif spec["source"] == "water_sample":
        usable = [t for t in recorded if _sample_is_near_trip(t)]
        reason = "old_samples" if len(usable) < len(recorded) else None
    return [float(t[spec["column"]]) for t in usable], reason


# -- comparing ---------------------------------------------------------------------


def _position(spec: dict, current: float, low: float, high: float) -> MatchPosition:
    if low <= current <= high:
        return "inside"
    nearer = low if current < low else high
    margin = spec.get("margin", 0.0) + spec.get("relative_margin", 0.0) * abs(nearer)
    if abs(current - nearer) <= margin:
        return "near"
    return "below" if current < low else "above"


def _left_out(field: str, reason: str, empty_reason: str | None = None) -> MatchLeftOut:
    return MatchLeftOut(
        field=field,
        label=_FIELD_SPECS[field]["label"],
        reason=reason,
        text=labels.MATCH_LEFT_OUT_REASONS[reason],
        empty_reason=empty_reason,
    )


def _comparison(spec: dict, today: float, values: list[float]) -> MatchComparison:
    field = spec["field"]
    unit = _FIELD_SPECS[field]["unit"]
    label = _FIELD_SPECS[field]["label"]
    low, high = min(values), max(values)
    position = _position(spec, today, low, high)
    text = labels.MATCH_COMPARISON_LINE.format(
        label=label,
        range=_range(low, high, unit),
        current=_amount(today, unit),
        position=labels.MATCH_POSITION_LABELS[position],
    )
    return MatchComparison(
        field=field,
        label=label,
        unit=unit,
        current=today,
        low=low,
        high=high,
        trips=len(values),
        position=position,
        text=text,
    )


def _summary(best: list[dict]) -> str:
    months = _month_list(_months(best))
    if len(best) == 1:
        template = (
            labels.MATCH_BEST_TRIP_SUMMARY if months else labels.MATCH_BEST_TRIP_SUMMARY_NO_MONTHS
        )
    else:
        template = (
            labels.MATCH_BEST_TRIPS_SUMMARY if months else labels.MATCH_BEST_TRIPS_SUMMARY_NO_MONTHS
        )
    return template.format(n=len(best), months=months)


def _status(compared: int, inside: int) -> str:
    if compared < labels.MIN_COMPARED_CONDITIONS:
        return "not_enough_readings"
    share = inside / compared
    if share >= labels.MATCH_SIMILAR_FRACTION:
        return "similar"
    if share >= labels.MATCH_PARTLY_FRACTION:
        return "partly"
    return "different"


def _assemble(
    pin_id: int,
    rated: list[dict],
    *,
    status: str,
    best: list[dict],
    headline: str,
    comparisons: list[MatchComparison],
    left_out: list[MatchLeftOut],
) -> PinMatch:
    inside = sum(1 for c in comparisons if c.position in ("inside", "near"))
    compared = len(comparisons)
    parts = []
    if best:
        parts.append(_summary(best))
    parts.extend(c.text for c in comparisons)
    parts.append(headline)
    if left_out:
        items = ", ".join(f"{item.label} ({item.text})" for item in left_out)
        parts.append(labels.MATCH_LEFT_OUT_LINE.format(items=items))
    return PinMatch(
        pin_id=pin_id,
        label=labels.MATCH_INDICATOR_LABEL,
        shown=True,
        status=status,
        status_label=labels.MATCH_STATUS_LABELS[status],
        rated_trips=len(rated),
        min_rated_trips=labels.MIN_RATED_TRIPS_FOR_MATCH,
        best_trips=len(best),
        best_months=_months(best),
        compared=compared,
        inside=inside,
        score=round(inside / compared, 4) if compared else None,
        comparisons=comparisons,
        left_out=left_out,
        headline=headline,
        reason=" ".join(parts),
    )


def evaluate(pin_id: int, rated: list[dict], today: dict[str, Reading]) -> PinMatch:
    """The match for one pin, from its rated trips and today's readings.

    Pure: no database and no lookups, so every rule is testable on its own.
    `today` maps a condition field to its reading; a field absent from it is
    unavailable.
    """
    if len(rated) < labels.MIN_RATED_TRIPS_FOR_MATCH:
        headline = hidden_headline(len(rated))
        return PinMatch(
            pin_id=pin_id,
            label=labels.MATCH_INDICATOR_LABEL,
            shown=False,
            status="hidden",
            rated_trips=len(rated),
            min_rated_trips=labels.MIN_RATED_TRIPS_FOR_MATCH,
            headline=headline,
            reason=headline,
        )

    best = best_trips(rated)
    if len(best) < labels.MIN_TRIPS_TO_COMPARE:
        good = sum(1 for t in rated if t["rating"] >= labels.MIN_BEST_RATING)
        headline = labels.MATCH_HEADLINES["few_good_trips"].format(
            good=good,
            rated=len(rated),
            rating=labels.RATING_LABELS[labels.MIN_BEST_RATING],
        )
        return _assemble(
            pin_id,
            rated,
            status="few_good_trips",
            best=[],
            headline=headline,
            comparisons=[],
            left_out=[],
        )

    comparisons: list[MatchComparison] = []
    left_out: list[MatchLeftOut] = []
    for spec in labels.MATCH_VARIABLES:
        field = spec["field"]
        reading = today.get(field) or Reading(value=None)
        if reading.value is None or isinstance(reading.value, str):
            left_out.append(_left_out(field, "unavailable", reading.empty_reason))
            continue
        if reading.stale:
            left_out.append(_left_out(field, "stale"))
            continue
        values, dropped = _history(spec, best, reading)
        if len(values) < labels.MIN_TRIPS_TO_COMPARE:
            left_out.append(_left_out(field, dropped or "too_few_trips"))
            continue
        comparisons.append(_comparison(spec, float(reading.value), values))

    inside = sum(1 for c in comparisons if c.position in ("inside", "near"))
    status = _status(len(comparisons), inside)
    headline = labels.MATCH_HEADLINES[status].format(inside=inside, compared=len(comparisons))
    return _assemble(
        pin_id,
        rated,
        status=status,
        best=best,
        headline=headline,
        comparisons=comparisons,
        left_out=left_out,
    )


# -- today's readings --------------------------------------------------------------


def _todays_readings(db: Database, user_id: int, pin: dict) -> dict[str, Reading]:
    """The panel's readings for this pin, as the match sees them."""
    ctx = describe(db, lat=pin["lat"], lng=pin["lng"], caller="pin_panel", user_id=user_id)
    if ctx is None:
        return {}
    out = {}
    for spec in labels.MATCH_VARIABLES:
        field = getattr(ctx.conditions, spec["field"])
        prov = field.provenance
        out[spec["field"]] = Reading(
            value=field.value,
            stale=field.stale,
            empty_reason=field.empty_reason.value if field.empty_reason else None,
            station=prov.station if prov else None,
        )
    return out


def match_for_pin(db: Database, user_id: int, pin: dict) -> PinMatch:
    """The match for one of the user's own pins (`pin` as store.get_pin returned it).

    Today's readings are looked up only when the pin has enough rated trips
    and enough good ones to compare against: a hidden or empty match makes no
    outside call.
    """
    columns = tuple(spec["column"] for spec in labels.MATCH_VARIABLES)
    rated = store.rated_trips(db, user_id, pin["id"], columns)
    if (
        len(rated) < labels.MIN_RATED_TRIPS_FOR_MATCH
        or len(best_trips(rated)) < labels.MIN_TRIPS_TO_COMPARE
    ):
        return evaluate(pin["id"], rated, {})
    return evaluate(pin["id"], rated, _todays_readings(db, user_id, pin))


def pin_match(db: Database, user_id: int, pin_id: int) -> dict | None:
    """GET /pins/{id}/match, or None if the pin is not the user's."""
    pin = store.get_pin(db, user_id, pin_id)
    if pin is None:
        return None
    return match_for_pin(db, user_id, pin).model_dump()
