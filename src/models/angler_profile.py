"""The angler profile: how FishDex presents itself to one person.

Groundwork, not a finished model. Two independent signals, both derived from
logged behaviour and recomputed once per logged session:

  * an **orientation spectrum** from specialist (0.0) to lifelister (1.0),
    with a confidence so early data does not swing the UI, plus two separate
    soft values — size focus and small-species (microfishing) focus — that are
    not part of the spectrum;
  * **literacy per concept** (one state per term_id in `services/terms.py`),
    never a global level. There is no beginner/expert label anywhere.

Every value is stored as value + source (inferred | declared) + weight, so a
future questionnaire can write a declared starting value that fades as
behaviour accumulates, and a settings override can pin one. A profile never
filters facts: it only changes emphasis and wording.
"""

from typing import Literal

from pydantic import BaseModel, Field

ValueSource = Literal["inferred", "declared"]
ConceptStateName = Literal["unseen", "introduced", "familiar"]

# The only keys a declared value may be written to. A questionnaire can seed
# these and nothing else — in particular, no skill or expertise field.
SCALAR_KEYS: tuple[str, ...] = ("orientation", "size_focus", "micro_focus")
CONCEPT_KEY_PREFIX = "concept:"

# A concept state as a number, for storage alongside the scalar values.
CONCEPT_STATE_VALUE: dict[str, float] = {"unseen": 0.0, "introduced": 0.5, "familiar": 1.0}


class ProfileValue(BaseModel):
    """One stored value from one source."""

    value: float = Field(ge=0.0, le=1.0)
    source: ValueSource
    weight: float = Field(ge=0.0)
    """Inferred: the decayed evidence weight behind the value. Declared: the
    starting weight, which fades as sessions are logged after it."""

    is_override: bool = False
    """Set from settings. An override does not fade; clearing it hands the
    value back to behaviour."""

    updated_at: str | None = None


class BlendedValue(BaseModel):
    """What presentation reads: the inferred and declared parts, blended."""

    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    inferred: ProfileValue | None = None
    declared: ProfileValue | None = None


class ConceptEvidence(BaseModel):
    """One kind of evidence behind a concept's state."""

    kind: Literal["logged_near", "saved_near", "definition_opened", "seen_on_map"]
    count: int = 0
    sessions: int = 0
    """Distinct logged sessions — only meaningful for `logged_near`."""
    refs: list[str] = Field(default_factory=list)
    """A few ids behind the count (stop ids, pin ids, spot ids), for sanity checks."""


class ConceptState(BaseModel):
    term_id: str
    state: ConceptStateName = "unseen"
    source: ValueSource = "inferred"
    evidence: list[ConceptEvidence] = Field(default_factory=list)


class SessionEvidence(BaseModel):
    """What one logged session contributed to the spectrum, and why.

    Kept on the profile so the output can be sanity-checked against the trips
    that produced it: a number nobody can trace back is a number nobody can
    correct.
    """

    session_id: int
    date: str | None = None
    places: list[str] = Field(default_factory=list)
    lifelist: float = 0.0
    specialist: float = 0.0
    signal: float | None = None
    """lifelist / (lifelist + specialist): 1.0 is all lifelist evidence. None
    when the session carried no evidence either way (it then moves nothing)."""
    weight: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    richness_basis: list[str] = Field(default_factory=list)


class AnglerProfile(BaseModel):
    user_id: int
    orientation: BlendedValue
    size_focus: BlendedValue
    micro_focus: BlendedValue
    concepts: dict[str, ConceptState] = Field(default_factory=dict)
    sessions_used: int = 0
    session_evidence: list[SessionEvidence] = Field(default_factory=list)
    computed_at: str | None = None
