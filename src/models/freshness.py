"""How old a reading is, and when it is too old to trust.

The stale limits are named constants and they live here, in one place, because
they are a judgement about each source and not a property of any one caller.
Weather moves in hours, a river gauge in hours too but more slowly, and a
water-quality grab sample is a snapshot of one afternoon that stays useful for
weeks. A value past its limit is still shown, marked stale; it is excluded from
anything that matches current conditions against the user's history.

Times are carried as ISO-8601 text and compared as UTC. A time with no offset
is read as UTC, which is what Open-Meteo returns by default and is harmless
for the 30-day water-quality limit, where an offset is smaller than the limit's
resolution.
"""

from datetime import UTC, date, datetime

WEATHER_STALE_S = 2 * 60 * 60
"""Open-Meteo "current" values are model output refreshed every 15 minutes."""

GAUGE_STALE_S = 6 * 60 * 60
"""A WSC gauge reports about every 5 minutes; six hours old means it stopped."""

WATER_QUALITY_STALE_S = 30 * 24 * 60 * 60
"""PWQMN grab samples. Almost always past this: the newest sample is typically
months old, so the panel will usually say stale, and that is the point."""


SNAPSHOT_MAX_GAP_S = 24 * 60 * 60
"""A gauge reading is stored as a trip's flow only if it was taken within this
long of the trip. A trip logged days later would otherwise be stamped with a
river it never fished; its columns stay NULL instead. The reading's own time is
stored beside it, so the real gap is always recoverable."""


def parse_observed_at(text: str | None) -> datetime | None:
    """An ISO date or datetime as an aware UTC datetime, or None if unreadable."""
    if not text:
        return None
    raw = str(text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalise_observed_at(value: datetime | date | str | None) -> str | None:
    """The canonical text for an observation time.

    A bare date stays a bare date: padding it to midnight would claim an hour
    nobody measured. A datetime becomes an aware UTC ISO string.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) == 10 and parse_observed_at(text) is not None:
        return text
    parsed = parse_observed_at(text)
    return parsed.isoformat() if parsed else None


def age_seconds(observed_at: str | None, now: datetime | None = None) -> int | None:
    """Seconds since the observation, never negative; None when it has no usable time."""
    observed = parse_observed_at(observed_at)
    if observed is None:
        return None
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return max(0, int((current - observed).total_seconds()))


def is_stale(observed_at: str | None, limit_s: int, now: datetime | None = None) -> bool:
    """True when older than the limit — or when there is no time to judge by.

    An undated reading cannot be shown to be fresh, so it is treated as stale.
    """
    age = age_seconds(observed_at, now)
    return age is None or age > limit_s
