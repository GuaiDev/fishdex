"""Raw LIO bathymetry records: a lake's survey index and its depth contours.

Two layers of the same Ontario government dataset (Land Information Ontario,
"Bathymetry"), read straight from the source and stored as the evidence a
drop-off or hump is later derived from:

- **Bathymetry Index** (layer 31) — one polygon per surveyed lake, with the
  project name, the survey year and method, and the link id the source uses.
- **Bathymetry Line** (layer 30) — the depth contours themselves: each line
  holds one `DEPTH` and the survey facts behind it.

Neither is shown to a person on its own; the map shows the features derived from
them (services/bathymetry.py). Two facts follow from the source and are recorded
here rather than guessed:

- `DEPTH` is in metres relative to the lake surface **at the survey's own water
  level**, not a current depth. The source states no unit; metres is inferred
  from the value range. That caveat is a data note, never a card line.
- A `SURVEY_DATE` is an epoch-millisecond timestamp, and is often absent (the
  oldest surveys predate it). `survey_date` is the ISO date or None; the year the
  map shows is read from it or from the index's `survey_year`.
"""

from typing import Literal

from pydantic import BaseModel, field_validator

from src.models.jurisdiction import JurisdictionCode

# How a contour reached us: read straight from the source survey, never modelled.
LIO_BATHYMETRY_SOURCE = "LIO Bathymetry"

LineGeometryType = Literal["LineString", "MultiLineString"]


class BathymetryIndex(BaseModel):
    """One surveyed lake's index polygon from LIO Bathymetry (layer 31).

    The polygon marks the area the survey covered; it is matched to a lake
    outline by overlap, never by project name (names repeat across water).
    """

    ogf_id: int
    project_name: str | None = None
    wby_lid: str | None = None
    """The source's own lake link id, kept for reference; not used to match."""
    survey_year: int | None = None
    scale_text: str | None = None
    survey_method: str | None = None
    bathymetry_line_ind: bool | None = None
    """True when line (contour) data exists for this survey, not just an index."""
    bathymetry_point_ind: bool | None = None
    historic_map_ind: bool | None = None
    effective_datetime: str | None = None
    geometry: dict
    """GeoJSON Polygon or MultiPolygon of the surveyed extent."""
    jurisdiction: JurisdictionCode = "CA-ON"
    source: str = LIO_BATHYMETRY_SOURCE
    licence: str | None = None

    @field_validator("geometry")
    @classmethod
    def _geometry_is_a_polygon(cls, v: dict) -> dict:
        if v.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("a bathymetry index geometry must be a Polygon or MultiPolygon")
        if not v.get("coordinates"):
            raise ValueError("a bathymetry index geometry must not be empty")
        return v


class BathymetryContour(BaseModel):
    """One depth contour from LIO Bathymetry (layer 30).

    `depth_m` is metres below the survey's own water level, so it is always zero
    or negative (0 is where the contour meets the shore). The survey facts ride
    on the line: the date it was run, the method, and how it was digitised.
    """

    ogf_id: int
    depth_m: float
    survey_method: str | None = None
    survey_date: str | None = None
    """ISO date of the survey, from SURVEY_DATE (epoch ms); None when absent."""
    creation_methodology: str | None = None
    location_accuracy: str | None = None
    geometry: dict
    """GeoJSON LineString or MultiLineString: one contour at one depth."""
    water_body_id: str | None = None
    """The lake this contour belongs to, set when it is matched to an outline."""
    jurisdiction: JurisdictionCode = "CA-ON"
    source: str = LIO_BATHYMETRY_SOURCE
    licence: str | None = None
    built_at: str | None = None

    @field_validator("depth_m")
    @classmethod
    def _depth_is_at_or_below_the_surface(cls, v: float) -> float:
        if v > 0:
            raise ValueError(f"a depth contour cannot be above the surface, got {v}")
        return v

    @field_validator("geometry")
    @classmethod
    def _geometry_is_a_line(cls, v: dict) -> dict:
        if v.get("type") not in ("LineString", "MultiLineString"):
            raise ValueError("a bathymetry contour must be a LineString or MultiLineString")
        if not v.get("coordinates"):
            raise ValueError("a bathymetry contour geometry must not be empty")
        return v
