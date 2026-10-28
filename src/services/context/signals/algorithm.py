"""The one algorithm: a candidate place in, a plain weighted fit out.

    score = weighted geometric mean of each active signal's fit,
            weight = that signal's confidence

A geometric mean because every active signal must roughly agree: an explorer
shown a place they have fished many times, or a pond angler shown a creek, is
pulled below the bar by the one signal that disagrees, which an arithmetic mean
would let three agreeing signals paper over. No learning, no feedback loop: the
same profile and candidate always give the same answer, and every number in it
can be read back in the reasons.

A signal is *active* only when it has enough behind it. Behaviour alone must not
narrow the profile early, so an undeclared reading needs `BEHAVIOUR_ONLY_MIN`
confidence (about six events). A declared value, which the angler said on
purpose, needs only `WITH_DECLARED_MIN`. With nothing active the answer is "no
fit available" (`score=None`), never a default.

Nothing here reads catch counts, sizes or species: a fit says how well the
water suits the angler's own pattern, and never what they will catch.
"""

import math

from src.models.angler_profile import SignalReading
from src.models.angler_signals import Personalisation, SignalFit, SpotCandidate, SpotFit
from src.services.context.signals import words
from src.services.context.signals.registry import REGISTRY

BEHAVIOUR_ONLY_MIN = 0.5
WITH_DECLARED_MIN = 0.3
KEEP_AT = 0.65
REASON_AT = 0.5  # a fit has to be at least this good to be given as a reason
_FLOOR = 0.01  # keeps one zero from erasing the log of everything else


def is_active(reading: SignalReading) -> bool:
    if reading.signal_id not in REGISTRY:
        return False
    needed = WITH_DECLARED_MIN if reading.declared is not None else BEHAVIOUR_ONLY_MIN
    return reading.confidence >= needed


def active_signals(signals: dict[str, SignalReading]) -> list[SignalReading]:
    return [r for r in signals.values() if is_active(r)]


def fit_candidate(signals: dict[str, SignalReading], candidate: SpotCandidate) -> SpotFit:
    fits: list[tuple[float, SignalFit]] = []
    for reading in active_signals(signals):
        result = REGISTRY[reading.signal_id].fit(reading, candidate)
        if result is not None:
            fits.append((reading.confidence, result))
    if not fits:
        return SpotFit(spot_id=candidate.spot_id)
    total = sum(w for w, _ in fits)
    score = math.exp(sum(w * math.log(max(f.score, _FLOOR)) for w, f in fits) / total)
    reasons = [
        f.reason for _, f in sorted(fits, key=lambda wf: -wf[1].score) if f.score >= REASON_AT
    ]
    return SpotFit(
        spot_id=candidate.spot_id,
        score=round(score, 4),
        keep=score >= KEEP_AT,
        reasons=reasons,
        fits=[f for _, f in fits],
        signals_used=[f.signal_id for _, f in fits],
    )


def personalisation(signals: dict[str, SignalReading]) -> Personalisation:
    """Which signals took part for this angler, for a response's `meta`."""
    active = active_signals(signals)
    if not active:
        return Personalisation(active=False, note=words.NOT_PERSONALISED)
    return Personalisation(
        active=True,
        confidence=round(sum(r.confidence for r in active) / len(active), 4),
        signals=[
            {"signal_id": r.signal_id, "confidence": r.confidence, "summary": r.summary}
            for r in active
        ],
    )
