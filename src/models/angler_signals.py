"""Models for fitting a place to an angler: the candidate in, the fit out.

A signal (see `models/angler_profile.SignalReading`) says something about the
angler. A *fit* says how well one candidate place matches what the signals say.
Nothing here predicts fish, size or catch outcomes: a candidate carries only
facts about the water and about the angler's own history there, so a fit can
never be read as a promise.
"""

from pydantic import BaseModel, Field


class SpotCandidate(BaseModel):
    """One place being considered, described only by what signals may look at."""

    spot_id: str
    water_type: str | None = None
    """river / creek / lake / pond / great_lake; None when the spot's water is unknown."""
    visits: int = 0
    """Distinct logged sessions at or next to this place."""
    recent_visits: float = 0.0
    """The same visits, each discounted by how long ago it was."""
    is_favourite: bool = False
    """Fished repeatedly and recently, or saved by the angler as a favourite."""


class SignalFit(BaseModel):
    """How well one candidate matches one signal."""

    signal_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    """A plain sentence in the angler's terms ('you mostly fish small creeks')."""


class SpotFit(BaseModel):
    """The combined fit of one candidate across every active signal."""

    spot_id: str
    score: float | None = None
    """None when no signal was active: there was nothing to fit against."""
    keep: bool = False
    reasons: list[str] = Field(default_factory=list)
    fits: list[SignalFit] = Field(default_factory=list)
    signals_used: list[str] = Field(default_factory=list)


class Personalisation(BaseModel):
    """What the angler's profile had to say, once per request, for the response `meta`."""

    active: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: list[dict] = Field(default_factory=list)
    """`{signal_id, confidence, summary}` for each signal that took part."""
    note: str | None = None
