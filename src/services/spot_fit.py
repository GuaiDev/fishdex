"""Fit a list of spots to one angler: the consumer of the signal layer.

`fit_spots` turns each spot into a `SpotCandidate` (its water type, and how
often and how recently the angler has fished at or next to it) and hands it to
the one algorithm in `services/context/signals`. This module knows no signal by
name: a signal registered later changes the answer without a change here.

Per-user and computed per request, like `new_to_you`. Nothing is stored.
"""

import math
from collections import defaultdict

from sqlite_utils import Database

from src.models.angler_signals import Personalisation, SpotCandidate, SpotFit
from src.models.spot import Spot
from src.services.context import angler_profile
from src.services.context.profile import logged_sessions
from src.services.context.signals import fit_candidate, is_active, personalisation
from src.services.context.signals.algorithm import REASON_AT
from src.services.context.signals.exploration import EXPLORER_AT, REGULAR_AT
from src.services.context.weights import decay
from src.storage.water_bodies import water_body_types

# A logged stop this close to a spot counts as having fished there.
VISIT_RADIUS_M = 750.0
# A favourite is fished repeatedly and not long ago: at least this many sessions,
# whose recency-discounted total is at least FAVOURITE_MIN_RECENT.
FAVOURITE_MIN_SESSIONS = 2
FAVOURITE_MIN_RECENT = 1.0
HALF_LIFE_SESSIONS = 20
# An angler who neither explores nor grinds has no fit that clears the hard bar
# for unvisited, favourite or once-fished spots alike, so they get the best few.
MIXED_TOP = 10

_CELL_DEG = 0.01  # ~1.1 km of latitude, wider than VISIT_RADIUS_M
_M_PER_DEG = 111_320.0


class AnglerHistory:
    """Where one angler has fished, for 'have they been near here' questions."""

    def __init__(self) -> None:
        self._stops: dict[tuple[int, int], list[tuple[float, float, int, int]]] = defaultdict(list)
        self._favourite_pins: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)

    @staticmethod
    def _cell(lat: float, lng: float) -> tuple[int, int]:
        return math.floor(lat / _CELL_DEG), math.floor(lng / _CELL_DEG)

    @classmethod
    def load(cls, db: Database, user_id: int) -> "AnglerHistory":
        history = cls()
        sessions = logged_sessions(db, user_id)
        for index, session in enumerate(sessions):
            age = len(sessions) - 1 - index
            for stop in session.stops:
                if stop.lat is not None and stop.lng is not None:
                    history._stops[cls._cell(stop.lat, stop.lng)].append(
                        (stop.lat, stop.lng, session.id, age)
                    )
        if "pins" in db.table_names():
            for lat, lng in db.execute(
                "SELECT lat, lng FROM pins WHERE user_id = ? AND is_favourite = 1", [user_id]
            ).fetchall():
                history._favourite_pins[cls._cell(lat, lng)].append((lat, lng))
        return history

    def _near(self, grid: dict, lat: float, lng: float):
        cy, cx = self._cell(lat, lng)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from grid.get((cy + dy, cx + dx), ())

    @staticmethod
    def _metres(lat: float, lng: float, lat2: float, lng2: float) -> float:
        return math.hypot(
            (lat2 - lat) * _M_PER_DEG,
            (lng2 - lng) * _M_PER_DEG * math.cos(math.radians(lat)),
        )

    def candidate(self, spot: Spot, water_type: str | None) -> SpotCandidate:
        age_by_session: dict[int, int] = {}
        for lat, lng, session_id, age in self._near(self._stops, spot.lat, spot.lng):
            if self._metres(spot.lat, spot.lng, lat, lng) <= VISIT_RADIUS_M:
                age_by_session[session_id] = min(age, age_by_session.get(session_id, age))
        recent = sum(decay(a, HALF_LIFE_SESSIONS) for a in age_by_session.values())
        visits = len(age_by_session)
        habit = visits >= FAVOURITE_MIN_SESSIONS and recent >= FAVOURITE_MIN_RECENT
        # A favourite pin makes a place a favourite only once the angler has been there:
        # a saved-but-never-fished pin is somewhere they still want to find out about.
        pinned = visits >= 1 and any(
            self._metres(spot.lat, spot.lng, lat, lng) <= VISIT_RADIUS_M
            for lat, lng in self._near(self._favourite_pins, spot.lat, spot.lng)
        )
        return SpotCandidate(
            spot_id=spot.spot_id,
            water_type=water_type,
            visits=visits,
            recent_visits=round(recent, 4),
            is_favourite=habit or pinned,
        )


def fit_spots(
    db: Database, user_id: int, spots: list[Spot]
) -> tuple[list[tuple[Spot, SpotFit]], Personalisation]:
    """Every spot with its fit, in the order given, plus what the profile had to say.

    When no signal is active the fits carry `score=None` and `keep=False`: there
    is nothing to fit against, and the caller must not substitute a default.
    """
    profile = angler_profile(db, user_id=user_id)
    summary = personalisation(profile.signals)
    if not summary.active:
        return [(s, SpotFit(spot_id=s.spot_id)) for s in spots], summary
    history = AnglerHistory.load(db, user_id)
    types = water_body_types(db, [s.water_body_id for s in spots if s.water_body_id])
    fitted = [
        (s, fit_candidate(profile.signals, history.candidate(s, types.get(s.water_body_id))))
        for s in spots
    ]
    explore = profile.signals.get("explore")
    if explore is not None and is_active(explore) and REGULAR_AT < explore.value < EXPLORER_AT:
        _keep_best_matches(fitted)
    return fitted, summary


def _keep_best_matches(fitted: list[tuple[Spot, SpotFit]]) -> None:
    """Mixed explorer/regular: rank by fit and keep the top few that suit the water.

    Every other signal has to agree (a creek is not shown to a pond angler);
    only the explore dial is relaxed, because for this angler it cannot clear a
    fixed bar for any kind of spot.
    """
    suits = [
        f
        for _, f in fitted
        if f.score is not None
        and all(x.score >= REASON_AT for x in f.fits if x.signal_id != "explore")
    ]
    suits.sort(key=lambda f: (-f.score, f.spot_id))
    for f in suits[:MIXED_TOP]:
        f.keep = True
