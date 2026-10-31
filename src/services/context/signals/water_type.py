"""Preferred water type: pond, creek, river, lake, ... as a share of where the angler goes.

Evidence is *where they went*, not what they caught: one count per distinct
water per logged session, plus saved pins at a lower weight. Catch outcomes are
deliberately not read, so a place is never preferred because it produced fish.
Older sessions and pins count less (half-life in sessions).

A stop's water type comes from the spots table: the nearest spot within
`STOP_TO_SPOT_M` that sits on a known water body gives its type. A stop with no
such spot is counted in `unresolved` and left out of the shares. It is never
guessed from a name or a size. Works anywhere spots have been built.
"""

from collections import defaultdict

from src.models.angler_profile import ProfileValue, SignalReading
from src.models.angler_signals import SignalFit, SpotCandidate
from src.services.context.signals import words
from src.services.context.signals.registry import Signal, SignalInputs, register
from src.services.context.weights import clip, confidence, decay
from src.storage.water_bodies import nearest_spot_water_type

SIGNAL_ID = "water_type"
HALF_LIFE_SESSIONS = 20
PIN_WEIGHT = 0.5  # a saved pin is interest, not a trip
STOP_TO_SPOT_M = 1500.0
# A type is "mostly" fished at this share or more of the evidence.
MOSTLY_SHARE = 0.5


def derive(inputs: SignalInputs) -> SignalReading | None:
    if not inputs.sessions and not inputs.pins:
        return None
    weights: dict[str, float] = defaultdict(float)
    trips: dict[str, int] = defaultdict(int)
    unresolved = 0
    cache: dict[tuple[float, float], str | None] = {}

    def type_at(lat: float | None, lng: float | None) -> str | None:
        if lat is None or lng is None:
            return None
        key = (round(lat, 4), round(lng, 4))
        if key not in cache:
            hit = nearest_spot_water_type(inputs.db, lat, lng, STOP_TO_SPOT_M)
            cache[key] = hit[0] if hit else None
        return cache[key]

    for index, session in enumerate(inputs.sessions):
        w = decay(inputs.age(index), HALF_LIFE_SESSIONS)
        seen: set[str] = set()
        for stop in session.stops:
            if stop.water in seen:
                continue
            seen.add(stop.water)
            wb_type = type_at(stop.lat, stop.lng)
            if wb_type is None:
                unresolved += 1
                continue
            weights[wb_type] += w
            trips[wb_type] += 1
    pins = 0
    for _pin_id, lat, lng, _favourite, since in inputs.pins:
        wb_type = type_at(lat, lng)
        if wb_type is None:
            unresolved += 1
            continue
        weights[wb_type] += PIN_WEIGHT * decay(since, HALF_LIFE_SESSIONS)
        pins += 1

    total = sum(weights.values())
    shares = {t: round(w / total, 4) for t, w in sorted(weights.items())} if total else {}
    top = max(shares.values()) if shares else 0.0
    return SignalReading(
        signal_id=SIGNAL_ID,
        kind="distribution",
        shares=shares,
        confidence=confidence(total),
        inferred=ProfileValue(value=clip(top), source="inferred", weight=round(total, 4))
        if shares
        else None,
        evidence=[f"{t}: {n} trips" for t, n in sorted(trips.items())]
        + ([f"{pins} saved pins"] if pins else []),
        unresolved=unresolved,
    )


def describe(reading: SignalReading) -> str | None:
    if not reading.shares:
        return words.WATER_TYPE_NONE
    ranked = sorted(reading.shares.items(), key=lambda kv: (-kv[1], kv[0]))
    (first, share), rest = ranked[0], ranked[1:]
    if share >= MOSTLY_SHARE or not rest:
        sentence = words.WATER_TYPE_MOSTLY.format(water=words.water_plural(first))
    else:
        sentence = words.WATER_TYPE_MIX.format(
            first=words.water_plural(first), second=words.water_plural(rest[0][0])
        )
    return words.EARLY_READ.format(sentence=sentence) if reading.confidence < 0.5 else sentence


def fit(reading: SignalReading, candidate: SpotCandidate) -> SignalFit | None:
    """1.0 for the water type fished most, scaled down by how much less the type is fished.

    No opinion (None) when the spot's water type is unknown or the reading has no
    shares: not knowing is not a mismatch.
    """
    if candidate.water_type is None or not reading.shares:
        return None
    ratio = reading.shares.get(candidate.water_type, 0.0) / max(reading.shares.values())
    water = words.water_plural(candidate.water_type)
    template = words.WATER_TYPE_MOSTLY if ratio >= 0.999 else words.WATER_TYPE_ALSO
    return SignalFit(
        signal_id=SIGNAL_ID, score=round(ratio, 4), reason=template.format(water=water)
    )


register(Signal(id=SIGNAL_ID, kind="distribution", derive=derive, fit=fit, describe=describe))
