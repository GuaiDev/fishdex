"""The conditions panel on a pin: what is true at its coordinates right now.

The facts come from the context layer's "pin_panel" bundle, so staleness,
provenance and the reasons for an empty value are decided in one place and not
re-derived here. This module only picks the fields the panel shows, in the
order the labels module gives, and flattens each into a row. It formats
nothing: wording comes from the labels module and, for meanings, from the
context layer's translate module.

A pin is read through the owner's user_id only. Another user's pin is
indistinguishable from a missing one.
"""

from datetime import UTC, datetime

from sqlite_utils import Database

from src.models.context import ContextField
from src.models.freshness import age_seconds
from src.models.pin import PinConditionRow
from src.services.context import describe
from src.services.pins.labels import CONDITION_FIELDS, age_label
from src.storage import pins as store


def _row(spec: dict, field: ContextField, now: datetime) -> PinConditionRow:
    prov = field.provenance
    observed_at = prov.observed_at if prov and not field.is_empty else None
    age = age_seconds(observed_at, now)
    return PinConditionRow(
        field=spec["field"],
        label=spec["label"],
        group=spec["group"],
        value=field.value,
        unit=spec["unit"] if not field.is_empty else None,
        meaning=field.meaning,
        source=prov.source if prov else None,
        station=prov.station if prov else None,
        distance_km=prov.distance_km if prov else None,
        observed_at=observed_at,
        age_s=age,
        age_label=age_label(age),
        stale=field.stale,
        empty_reason=field.empty_reason.value if field.empty_reason else None,
    )


def pin_conditions(db: Database, user_id: int, pin_id: int) -> list[dict] | None:
    """The panel for one of the user's pins, or None if it is not theirs."""
    pin = store.get_pin(db, user_id, pin_id)
    if pin is None:
        return None
    ctx = describe(db, lat=pin["lat"], lng=pin["lng"], caller="pin_panel", user_id=user_id)
    now = datetime.now(UTC)
    rows = [_row(spec, getattr(ctx.conditions, spec["field"]), now) for spec in CONDITION_FIELDS]
    return [r.model_dump() for r in rows]
