"""The spot card: what tapping a spot reveals, as one read model.

Four fixed blocks, every user-facing string already written (by `render_spot()`
in services/context/render.py and nowhere else), so a front end renders the
card and joins nothing: not the records, not the labels, not the access data.

    1. hook          why it's interesting, at most two lines
    2. might_find    species recorded within 2 km, with source and date
    3. why_gather    the kind's general reasoning, then facts about this spot
    4. getting_there parking, trail, launch, crossing, Crown land, and what is unverified

The card is the same for everyone. Nothing here is per-user: the "new to you"
and "special here" hooks are later phases and have no field yet.

Records and reasoning never share a field. Anything that came from a record
carries `provenance="record"` and its source; the general explanation carries
`provenance="inference"` and a fixed label.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

HookKind = Literal[
    "protected_species",
    "new_to_you",
    "distinctive",
    "structure",
    "honest_empty",
    "kind_description",
]
# The hook is two lines on a phone; about 80 characters a line.
HOOK_MAX_CHARS = 160


class CardHook(BaseModel):
    title: str
    kind: HookKind
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _two_lines_at_most(self):
        if len(self.text) > HOOK_MAX_CHARS:
            raise ValueError(f"a hook is at most two lines ({HOOK_MAX_CHARS} characters)")
        return self


class CardSpecies(BaseModel):
    """One species recorded within the radius, as the card shows it."""

    name: str  # common name when we have one
    scientific_name: str | None = None
    source: str  # "iNaturalist", "GBIF/<dataset>", "TRCA RWMP" ...
    date: str | None = None
    # "survey" is a standardised haul with known method; "sighting" is a photo,
    # a specimen or a casual record. Surveys come first.
    evidence: Literal["survey", "sighting"]
    cite: str  # "iNaturalist, 2024-06-03", ready to show in brackets
    note: str | None = None  # "Older record" when the date is a decade or more back


class MightFindBlock(BaseModel):
    title: str
    species: list[CardSpecies] = Field(default_factory=list)
    more_text: str | None = None  # "and 3 more species recorded nearby"
    caveat: str  # "Recorded nearby, not a catch guarantee."
    empty_text: str | None = None  # why there is no list
    note: str | None = None  # why some recorded species are not shown
    radius_km: float
    default_open: bool = True


class CardFact(BaseModel):
    """A fact about this spot from a record, tagged as one."""

    text: str
    provenance: Literal["record"] = "record"
    source: str


class GeneralReasoning(BaseModel):
    text: str
    label: str
    provenance: Literal["inference"] = "inference"


class WhyGatherBlock(BaseModel):
    title: str
    what_it_is: str  # the kind in the plain word
    general: GeneralReasoning
    facts_title: str
    facts: list[CardFact] = Field(default_factory=list)
    default_open: bool = False


class AccessItem(BaseModel):
    kind: Literal["parking", "trail_head", "boat_launch", "road_crossing"]
    label: str  # "Parking", "Boat launch"
    text: str  # "Parking about 150 m away"
    distance_m: int
    details: list[str] = Field(default_factory=list)  # "mapped as free", "asphalt"
    source: str = "OpenStreetMap"


class CrownLandFlag(BaseModel):
    state: Literal["mapped", "none_found", "not_checked"]
    text: str
    source: str | None = None


class GettingThereBlock(BaseModel):
    title: str
    items: list[AccessItem] = Field(default_factory=list)
    empty_text: str | None = None
    crown_land: CrownLandFlag
    unverified_title: str
    unverified: list[str] = Field(default_factory=list)
    default_open: bool = False


class GlossaryTerm(BaseModel):
    """A hard word on the card and its definition, from `services/terms.py`."""

    term: str
    term_id: str
    text: str


class CardMeta(BaseModel):
    """Counts that separate 'nothing there' from 'could not check', for the UI's
    Sources view and for tests. Not shown on the card face."""

    records_radius_km: float
    species_shown: int = 0
    species_withheld_protected: int = 0  # a count only; never names
    species_withheld_imprecise: int = 0  # records whose location is fuzzed ~22 km
    species_status_unchecked: int = 0  # not in the local species file
    cache_key: str  # "{spot_id}@{built_at}"; the same for every user


class SpotCard(BaseModel):
    spot_id: str
    hook: CardHook
    might_find: MightFindBlock
    why_gather: WhyGatherBlock
    getting_there: GettingThereBlock
    terms: list[GlossaryTerm] = Field(default_factory=list)
    meta: CardMeta
