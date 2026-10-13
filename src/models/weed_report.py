"""Pydantic models for weed reports: weed edges the user saw and wrote down.

A weed report is one person's sighting of where weeds start or stop on a water
body: a point, or a short line along the edge. It is user-reported and never
derived. No dataset locates submerged or emergent weed beds, so nothing here is
filled from wetland maps, plant records or an outline, and the report says what
it is on every read: reported, not verified.

Reports are private to the person who made them. There is no shared state in
these models: no visibility field, no share list, no count.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from src.models.jurisdiction import JurisdictionCode

# The only provenance a weed report can have. A column and a type, not a flag,
# so a derived report cannot be written by mistake.
WeedProvenance = Literal["user_reported"]

WeedNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

# A line along an edge is a few hundred points at most; more is a mistake.
MAX_LINE_VERTICES = 500


def _lng_lat(position: object) -> tuple[float, float]:
    if (
        not isinstance(position, (list, tuple))
        or len(position) != 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in position)
    ):
        raise ValueError("a position is [longitude, latitude]")
    lng, lat = float(position[0]), float(position[1])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90) or lng != lng or lat != lat:
        raise ValueError("longitude must be -180..180 and latitude -90..90, in that order")
    return lng, lat


class WeedGeometry(BaseModel):
    """A GeoJSON Point or LineString: where the reported weed edge is."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point", "LineString"]
    coordinates: list

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "Point":
            lng, lat = _lng_lat(self.coordinates)
            self.coordinates = [lng, lat]
            return self
        if not 2 <= len(self.coordinates) <= MAX_LINE_VERTICES:
            raise ValueError(f"a line needs 2 to {MAX_LINE_VERTICES} positions")
        line = [list(_lng_lat(p)) for p in self.coordinates]
        if len({tuple(p) for p in line}) < 2:
            raise ValueError("a line needs at least two different positions")
        self.coordinates = line
        return self


class WeedReportCreate(BaseModel):
    """POST /map/weed-reports."""

    model_config = ConfigDict(extra="forbid")

    water_body_id: str
    geometry: WeedGeometry
    observed_on: date
    note: WeedNote | None = None

    @field_validator("observed_on")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("observed_on cannot be in the future")
        return v


class WeedReportUpdate(BaseModel):
    """PATCH /map/weed-reports/{id}. Only what is sent changes.

    The water body cannot change: to move a report to another water, delete it
    and make a new one. `note: null` clears the note.
    """

    model_config = ConfigDict(extra="forbid")

    geometry: WeedGeometry | None = None
    observed_on: date | None = None
    note: WeedNote | None = None

    @model_validator(mode="after")
    def _sent_fields_are_not_null(self):
        for name in ("geometry", "observed_on"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        if self.observed_on is not None and self.observed_on > date.today():
            raise ValueError("observed_on cannot be in the future")
        return self


class WeedReport(BaseModel):
    """A weed report as stored in the weed_reports table."""

    id: int
    user_id: int
    water_body_id: str
    jurisdiction: JurisdictionCode
    geometry: WeedGeometry
    observed_on: date
    note: str | None = None
    provenance: WeedProvenance = "user_reported"
    created_at: str | None = None
    updated_at: str | None = None
    bbox: tuple[float, float, float, float] = Field(
        description="(min_lon, min_lat, max_lon, max_lat) of the geometry"
    )
