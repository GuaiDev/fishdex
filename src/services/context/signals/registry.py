"""The signal registry: the one place a new signal is added.

A signal is a small, self-contained reading of the angler taken from logged
behaviour (trips, catches, pins). To add one:

  1. write a module with a `derive(inputs) -> SignalReading | None` and a
     `fit(reading, candidate) -> SignalFit | None`;
  2. call `register(Signal(...))` in it;
  3. import the module in `signals/__init__.py`.

Nothing else changes. The profile derivation, the declared-value blending, the
cache version, the `/profile` response and every consumer of `fit_candidate`
read this registry, so none of them names a signal. A consumer that wants "how
well does this place suit this angler" asks the algorithm and gets one answer
built from whatever is registered.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlite_utils import Database

from src.models.angler_profile import SignalKind, SignalReading
from src.models.angler_signals import SignalFit, SpotCandidate


@dataclass
class SignalInputs:
    """Everything a signal may read, loaded once per recompute.

    `sessions` is oldest first; each has `.id`, `.season` and `.stops`, and each
    stop `.id`, `.lat`, `.lng`, `.water` (a stable key for the water) and
    `.label`. `pins` are `(pin_id, lat, lng, is_favourite, sessions_since)` for
    this user, where `sessions_since` is how many sessions were logged after the
    pin was saved (its age, in the same unit trips are aged in).
    """

    db: Database
    user_id: int
    sessions: list[Any]
    pins: list[tuple[int, float, float, bool, int]] = field(default_factory=list)

    def age(self, index: int) -> int:
        """Sessions logged after session number `index` (0 = oldest)."""
        return len(self.sessions) - 1 - index


@dataclass(frozen=True)
class Signal:
    id: str
    kind: SignalKind
    derive: Callable[[SignalInputs], SignalReading | None]
    fit: Callable[[SignalReading, SpotCandidate], SignalFit | None]
    describe: Callable[[SignalReading], str | None]
    declarable: bool = False
    """Whether the angler may declare or override it (`PUT /profile/declared/signal:<id>`).
    Only scalar signals can be: a declared value is a dial setting."""
    prior_value: float = 0.5
    prior_weight: float = 8.0
    """For a declarable scalar: the neutral pseudo-evidence its inferred part starts from."""


REGISTRY: dict[str, Signal] = {}


def register(signal: Signal) -> Signal:
    if signal.id in REGISTRY:
        raise ValueError(f"signal already registered: {signal.id!r}")
    if signal.declarable and signal.kind != "scalar":
        raise ValueError("only a scalar signal can be declared")
    REGISTRY[signal.id] = signal
    return signal


def derive_signals(inputs: SignalInputs) -> dict[str, SignalReading]:
    """Every registered signal's reading of this angler; a signal with no opinion is absent."""
    out: dict[str, SignalReading] = {}
    for signal in REGISTRY.values():
        reading = signal.derive(inputs)
        if reading is not None:
            reading.summary = signal.describe(reading)
            out[signal.id] = reading
    return out
