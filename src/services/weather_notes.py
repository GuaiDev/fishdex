"""Weather-behaviour notes: the "what pressure might mean for the bite" line.

The weather facts (pressure, its trend, temperature, wind) go to everyone. The
note about how fish are said to behave in them is a different kind of thing:
it is angler lore, not a measurement, so it is shown only to someone still
learning the concept it explains, and worded as a rule of thumb.

Who sees a note comes from the per-concept literacy in the angler profile
(`barometric_pressure` in `services/terms.py`): unseen and introduced get it,
familiar does not. There is no overall level involved and nothing is ever
removed from the facts. Showing a note is exposure evidence for that concept,
through the same `seen_on_map` event the profile already counts (5 sightings
introduce a concept, 50 make it familiar) — no advancement rule of its own.

Nothing here is specific to a place: a note is keyed by the concept and the
trend, so the same table serves every province and state.

Sourcing. A note carries a `source` only where a citable one exists. For
barometric pressure none was found that holds up: the popular claims trace to
angling magazines, and the peer-reviewed work reports mixed or correlational
results. So each note says it is a rule of thumb that studies do not settle,
and `source` is None rather than a link nobody checked. A trend with no
hedgeable claim ("steady") has no note at all.
"""

import logging
from dataclasses import dataclass

from sqlite_utils import Database

logger = logging.getLogger(__name__)

PRESSURE_TERM_ID = "barometric_pressure"
# Concept states that are still new to the concept, and so get the note.
NOTE_STATES = frozenset({"unseen", "introduced"})
# Reuses the profile's existing "seen repeatedly" evidence kind.
EXPOSURE_KIND = "seen_on_map"
EXPOSURE_REF = "weather_note"


@dataclass(frozen=True)
class WeatherNote:
    term_id: str
    text: str
    basis: str
    """What the claim rests on: always `angler_rule_of_thumb` today."""
    source: str | None
    """A citation, or None when no citable source exists. Never a guess."""

    def as_dict(self) -> dict:
        return {
            "term_id": self.term_id,
            "text": self.text,
            "basis": self.basis,
            "source": self.source,
        }


_PRESSURE_NOTES: dict[str, WeatherNote] = {
    "falling": WeatherNote(
        term_id=PRESSURE_TERM_ID,
        text=(
            "Many anglers like to fish while the pressure is falling, before a weather "
            "change, and some report more activity then. Studies of this are mixed, so "
            "treat it as a rule of thumb, not a promise of a better bite."
        ),
        basis="angler_rule_of_thumb",
        source=None,
    ),
    "rising": WeatherNote(
        term_id=PRESSURE_TERM_ID,
        text=(
            "Some anglers find the bite slows for a while once pressure climbs after a "
            "weather system has passed. Studies of this are mixed, so treat it as a rule "
            "of thumb, not a prediction."
        ),
        basis="angler_rule_of_thumb",
        source=None,
    ),
}


def concept_state(db: Database, user_id: int | None, term_id: str = PRESSURE_TERM_ID) -> str:
    """The user's literacy state for one concept. No user, or no profile: unseen.

    A failed profile read must not cost anyone the weather, and it must not
    silently drop the note either — so it falls back to unseen and says so.
    """
    if user_id is None:
        return "unseen"
    try:
        from src.services.context import angler_profile

        c = angler_profile(db, user_id=user_id).concepts.get(term_id)
    except Exception:  # noqa: BLE001
        logger.warning("could not read literacy for user %s", user_id, exc_info=True)
        return "unseen"
    return c.state if c else "unseen"


def note_for_trend(
    trend: str | None, state: str, term_id: str = PRESSURE_TERM_ID
) -> WeatherNote | None:
    """The note for this trend, or None when there is none or the concept is familiar."""
    if state not in NOTE_STATES:
        return None
    return _PRESSURE_NOTES.get(trend or "") if term_id == PRESSURE_TERM_ID else None


def note_for_user(
    db: Database, user_id: int | None, trend: str | None, *, record_exposure: bool = False
) -> WeatherNote | None:
    """The note this user should see for a trend, or None.

    With `record_exposure`, a shown note is logged as one sighting of the
    concept. Callers pass it only where the note really reaches the person
    (the chat agent's reply), not for a passive panel refresh.
    """
    note = note_for_trend(trend, concept_state(db, user_id))
    if note and record_exposure and user_id is not None:
        try:
            from src.storage import angler_profile as store

            store.record_event(db, user_id, note.term_id, EXPOSURE_KIND, EXPOSURE_REF)
        except Exception:  # noqa: BLE001
            logger.warning("could not record weather-note exposure", exc_info=True)
    return note
