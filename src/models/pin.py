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
