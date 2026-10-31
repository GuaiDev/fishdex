"""The three small pieces of arithmetic every profile value shares.

Kept in one place so the orientation spectrum and every registered signal decay
old evidence and turn accumulated weight into confidence the same way.
"""

import math

CONFIDENCE_SCALE = 8.0  # accumulated weight at which confidence is ~0.63


def decay(sessions: float, half_life: float) -> float:
    """How much evidence counts after `sessions` more sessions have been logged."""
    return 0.5 ** (sessions / half_life)


def confidence(weight: float) -> float:
    return round(1 - math.exp(-weight / CONFIDENCE_SCALE), 4)


def clip(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), 4)
