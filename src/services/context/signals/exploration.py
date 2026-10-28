"""The explore dial: 1.0 an explorer who keeps moving to new water, 0.0 a regular
who goes back to the same few places.

Inferred from trips alone. Each logged session after the first contributes one
observation per distinct water: new to the angler (1) or somewhere they had
fished before (0). The first session says nothing, since everything is new on a
first visit. Older sessions count less. Catches are not read.

The dial is also *declarable*: a questionnaire answer is a starting value that
fades as trips accumulate, a settings override holds until cleared (same rules
as orientation, see `profile.blend`). The consumer reads the blended value and
never knows which part came from where.
"""

from src.models.angler_profile import ProfileValue, SignalReading
from src.models.angler_signals import SignalFit, SpotCandidate
from src.services.context.signals import words
from src.services.context.signals.registry import Signal, SignalInputs, register
from src.services.context.weights import clip, confidence, decay

SIGNAL_ID = "explore"
HALF_LIFE_SESSIONS = 20
PRIOR_VALUE = 0.5
PRIOR_WEIGHT = 3.0  # pseudo-sessions at 0.5: light, so a declared answer is not diluted away
EXPLORER_AT = 0.6
REGULAR_AT = 0.4
# A place fished once is neither new nor a habit; it scores this share of a favourite's.
VISITED_ONCE_FACTOR = 0.5


def derive(inputs: SignalInputs) -> SignalReading | None:
    if len(inputs.sessions) < 2:
        return None
    visited: set[str] = {s.water for s in inputs.sessions[0].stops}
    new_weight = total = 0.0
    new_n = return_n = 0
    for index, session in enumerate(inputs.sessions[1:], start=1):
        w = decay(inputs.age(index), HALF_LIFE_SESSIONS)
        here = {s.water for s in session.stops}
        for water in here:
            if water in visited:
                return_n += 1
            else:
                new_weight += w
                new_n += 1
            total += w
        visited |= here
    if total == 0:
        return None
    raw = new_weight / total
    value = (PRIOR_VALUE * PRIOR_WEIGHT + new_weight) / (PRIOR_WEIGHT + total)
    return SignalReading(
        signal_id=SIGNAL_ID,
        kind="scalar",
        value=clip(value),
        confidence=confidence(total),
        inferred=ProfileValue(value=clip(raw), source="inferred", weight=round(total, 4)),
        evidence=[f"{new_n} visits to new water", f"{return_n} returns to known water"],
    )


def describe(reading: SignalReading) -> str | None:
    if reading.value is None:
        return None
    if reading.value >= EXPLORER_AT:
        sentence = words.EXPLORE_EXPLORER
    elif reading.value <= REGULAR_AT:
        sentence = words.EXPLORE_REGULAR
    else:
        sentence = words.EXPLORE_MIXED
    if reading.declared is not None:
        sentence += words.EXPLORE_DECLARED
    return words.EARLY_READ.format(sentence=sentence) if reading.confidence < 0.5 else sentence


def fit(reading: SignalReading, candidate: SpotCandidate) -> SignalFit | None:
    """An explorer is matched by unvisited places, a regular by their favourites.

    The explorer's score for a place is the dial itself; the regular's is its
    mirror. A place fished once sits low for both: it is neither new nor a habit.
    """
    if reading.value is None:
        return None
    e = reading.value
    if candidate.visits == 0:
        return SignalFit(signal_id=SIGNAL_ID, score=e, reason=words.REASON_UNVISITED)
    if candidate.is_favourite:
        return SignalFit(signal_id=SIGNAL_ID, score=1.0 - e, reason=words.REASON_FAVOURITE)
    return SignalFit(
        signal_id=SIGNAL_ID,
        score=(1.0 - e) * VISITED_ONCE_FACTOR,
        reason=words.REASON_VISITED_ONCE,
    )


register(
    Signal(
        id=SIGNAL_ID,
        kind="scalar",
        derive=derive,
        fit=fit,
        describe=describe,
        declarable=True,
        prior_value=PRIOR_VALUE,
        prior_weight=PRIOR_WEIGHT,
    )
)
