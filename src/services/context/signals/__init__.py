"""The signal layer: additive readings of the angler that plug into one algorithm.

Import this package to register every signal. Consumers use `fit_candidate` and
`personalisation`; the profile derivation uses `derive_signals`. See
`registry.py` for how to add a signal.
"""

from src.services.context.signals import exploration, water_type  # noqa: F401  (registers them)
from src.services.context.signals.algorithm import (
    active_signals,
    fit_candidate,
    is_active,
    personalisation,
)
from src.services.context.signals.registry import (
    REGISTRY,
    Signal,
    SignalInputs,
    derive_signals,
    register,
)

__all__ = [
    "REGISTRY",
    "Signal",
    "SignalInputs",
    "active_signals",
    "derive_signals",
    "fit_candidate",
    "is_active",
    "personalisation",
    "register",
]
