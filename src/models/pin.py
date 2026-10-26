"""Pydantic models for Personal-map pins.

A pin is one of the user's own places: a name, a point, a favourite flag and
optional notes. Its state is never stored. It is Saved until something links
to it and Visited after, where "something" is a logged stop or a recalled
"I've fished here before" record.

Ratings belong to trips. A pin never carries an averaged rating; it shows the
facts of the trips linked to it instead. A recalled record is kept apart from
logged trips in its own table so that no statistic can count it by accident.
"""

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# 1..5 maps to the anchored labels in src/services/pins/labels.py. A missing
# rating is None, never 0 and never the lowest label.
Rating = Annotated[int, Field(ge=1, le=5)]

LinkKind = Literal["confirmed", "inferred"]
PinState = Literal["saved", "visited"]
# "shared" is not a stored provenance: a shared pin (phase 4) is read through
# its share row, never copied into the recipient's pins.
PinProvenance = Literal["user", "imported"]

PinName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PinNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class Pin(BaseModel):
    """A pin as stored in the pins table."""

    id: int
    user_id: int
    name: PinName
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    jurisdiction: str  # ISO 3166-2, or "UNKNOWN" outside every registered box
    is_favourite: bool = False
    notes: str | None = None
    provenance: PinProvenance = "user"
    created_at: str | None = None
    updated_at: str | None = None


class PinCreate(BaseModel):
    """POST /pins. from_stop_id is "Pin this spot": that stop links as confirmed."""

    model_config = ConfigDict(extra="forbid")

    name: PinName
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    notes: PinNotes | None = None
    is_favourite: bool = False
    from_stop_id: int | None = None


class PinUpdate(BaseModel):
    """PATCH /pins/{id}. Only the fields sent are changed; notes may be cleared."""

    model_config = ConfigDict(extra="forbid")

    name: PinName | None = None
    notes: PinNotes | None = None
    is_favourite: bool | None = None

    @model_validator(mode="after")
    def _name_and_flag_cannot_be_cleared(self) -> "PinUpdate":
        for field in ("name", "is_favourite"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class PinMerge(BaseModel):
    """POST /pins/{id}/merge: this pin's trips and recalled records move to into_pin_id."""

    model_config = ConfigDict(extra="forbid")

    into_pin_id: int


class PinLinkCreate(BaseModel):
    """POST /pins/{id}/links: the user links one of their stops by hand."""

    model_config = ConfigDict(extra="forbid")

    stop_id: int


class RecalledVisitCreate(BaseModel):
    """POST /pins/{id}/recalled: "I've fished here before", without a full trip."""

    model_config = ConfigDict(extra="forbid")

    # Free text on purpose: "summer 2019", "a few years ago", "May 2021".
    date_approx: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = (
        None
    )
    species: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]] = Field(
        default_factory=list, max_length=50
    )
    rating: Rating | None = None

    @field_validator("species")
    @classmethod
    def _drop_blank_and_duplicate_species(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        kept: list[str] = []
        for name in value:
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                kept.append(name)
        return kept


class RecalledVisit(BaseModel):
    """A recalled record as stored. Provenance is always "recalled"."""

    id: int
    pin_id: int
    date_approx: str | None = None
    species: list[str] = Field(default_factory=list)
    rating: Rating | None = None
    provenance: Literal["recalled"] = "recalled"
    created_at: str | None = None


class SessionUpdate(BaseModel):
    """PATCH /sessions/{id}: the trip's rating and its start and end times.

    A field left out is unchanged; a field sent as null is cleared. Times must
    carry a timezone, so a duration never mixes local and UTC clocks.
    """

    model_config = ConfigDict(extra="forbid")

    rating: Rating | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "SessionUpdate":
        if self.started_at and self.ended_at and self.ended_at <= self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class TripPinRating(BaseModel):
    """PUT /sessions/{id}/pins/{pin_id}/rating: one pin's rating on a multi-pin trip."""

    model_config = ConfigDict(extra="forbid")

    rating: Rating | None


class PinConditionRow(BaseModel):
    """One row of GET /pins/{id}/conditions.

    Exactly one of `value` and `empty_reason` is set. A stale row still has its
    value: it is shown, marked, and left out of any match against the user's
    own history. `source`, `station`, `distance_km`, `observed_at` and `age_s`
    say where the value came from and how old it is; a gauge or sampling
    station can be on a different river from the pin, so its name and distance
    are always given. `station_water` names the watercourse the station is on and
    `on_pin_water` says whether that is the pin's own (None when the pin's water
    is not known); a gauge row's `label` already says so in words.
    """

    field: str
    label: str
    group: str
    value: float | str | None = None
    unit: str | None = None
    meaning: str | None = None
    source: str | None = None
    station: str | None = None
    distance_km: float | None = None
    station_water: str | None = None
    on_pin_water: bool | None = None
    observed_at: str | None = None
    age_s: int | None = None
    age_label: str | None = None
    stale: bool = False
    empty_reason: str | None = None

    @model_validator(mode="after")
    def _value_or_reason(self) -> "PinConditionRow":
        if (self.value is None) == (self.empty_reason is None):
            raise ValueError("a row has a value or an empty_reason, never both and never neither")
        return self


MatchStatus = Literal[
    "hidden",
    "similar",
    "partly",
    "different",
    "not_enough_readings",
    "few_good_trips",
]
MatchPosition = Literal["inside", "near", "below", "above"]
MatchLeftOutReason = Literal["stale", "unavailable", "too_few_trips", "other_gauge", "old_samples"]
TodaySort = Literal["match", "distance", "last_visited", "favourites"]


class MatchComparison(BaseModel):
    """One condition compared: today's reading against the user's best trips here."""

    field: str
    label: str
    unit: str | None = None
    current: float
    low: float
    high: float
    trips: int
    position: MatchPosition
    text: str

    @model_validator(mode="after")
    def _range_is_ordered(self) -> "MatchComparison":
        if self.low > self.high:
            raise ValueError("low cannot be above high")
        return self


class MatchLeftOut(BaseModel):
    """A condition that was not compared, and why."""

    field: str
    label: str
    reason: MatchLeftOutReason
    text: str
    empty_reason: str | None = None


class PinMatch(BaseModel):
    """GET /pins/{id}/match: how today compares with the user's own best trips here.

    Built only from the caller's trips at this pin that they rated. Recalled
    visits, other anglers' trips and shared pins never enter it, and a reading
    past its source's stale limit is left out, not compared.

    `status` "hidden" means the pin has fewer rated trips than the minimum:
    nothing else is filled in and the UI shows the conditions panel alone.
    `score` is the share of compared conditions inside or close to the range
    of the best trips, and is only a way to order pins: it is never shown as a
    probability of catching anything.
    """

    pin_id: int
    label: str
    shown: bool
    status: MatchStatus
    status_label: str | None = None
    rated_trips: int
    min_rated_trips: int
    best_trips: int = 0
    best_months: list[str] = Field(default_factory=list)
    compared: int = 0
    inside: int = 0
    score: float | None = Field(default=None, ge=0, le=1)
    comparisons: list[MatchComparison] = Field(default_factory=list)
    left_out: list[MatchLeftOut] = Field(default_factory=list)
    headline: str
    reason: str

    @model_validator(mode="after")
    def _hidden_carries_nothing_else(self) -> "PinMatch":
        if self.status == "hidden":
            if self.shown or self.comparisons or self.left_out or self.score is not None:
                raise ValueError("a hidden match shows nothing beyond its counts")
        elif not self.shown:
            raise ValueError("only a hidden match is not shown")
        return self


class TodayRow(BaseModel):
    """One of the user's own pins in the Today view, with why it is where it is."""

    rank: int
    pin_id: int
    name: str
    lat: float
    lng: float
    jurisdiction: str
    is_favourite: bool
    state: PinState
    visits: int
    last_visit: str | None = None
    distance_km: float | None = None
    reason: str = Field(min_length=1)
    match: PinMatch | None = None


class TodayResponse(BaseModel):
    """GET /pins/today."""

    sort: TodaySort
    sort_label: str
    rows: list[TodayRow]
