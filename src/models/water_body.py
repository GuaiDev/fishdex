"""Pydantic model for a water body: the river, creek or lake a spot sits on.

A spot is a reason to go to one place; a water body is the named water it is a
child of ("more spots on the Credit River"). It sits between the curated stretch
(a run of river someone drew) and the spot (a point on it): one river is several
stretches, one lake takes the mouths of many rivers.

Water bodies are *derived* (services/water_bodies.py) from the curated stretches
and from the lake names the spot classifier already resolves, and rebuilt with
the spots, never edited by hand. Nothing here is invented: a name that no source
gave stays empty with `name_source == "unknown"`, and a lake with no outline on
file has no geometry rather than a drawn guess.

A water body is a located thing, not a fish claim: no field says what lives in it.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.models.jurisdiction import JurisdictionCode

WaterBodyType = Literal["river", "creek", "lake", "pond", "great_lake"]

# Where the name came from, so a person's curated name reads apart from a
# fallback and an unnamed water reads as unnamed:
#   curated          the curation file's `river` or `lake` text, written by a person
#   curated_region   a stretch's `region`, used as the lake only when it is itself a known lake
#   ohn              a name carried by the Ontario Hydro Network data
#   osm              a name tagged on an OpenStreetMap element
#   unknown          no source gave one; `name` is None
NameSource = Literal["curated", "curated_region", "ohn", "osm", "unknown"]

# Where the drawn shape came from.
#   stretch_union  a river or creek: the simplified lines of its curated stretches
#   ohn_waterbody  a lake: matched from an OHN Waterbody polygon (OGL-Ontario)
#   none           no shape on file yet; the water is placed by its river mouths
GeometrySource = Literal["stretch_union", "ohn_waterbody", "none"]

_ID_PATTERN = re.compile(r"^(river|creek|lake|pond|great_lake):[a-z0-9]+(-[a-z0-9]+)*$")


class WaterBody(BaseModel):
    """A named (or knowingly unnamed) water, as stored in `water_bodies` and served to the map."""

    # `{type}:{slug of the name}`; stable across rebuilds because it comes from
    # the water's own name, not from a stretch id or a list position.
    water_body_id: str
    jurisdiction: JurisdictionCode
    name: str | None = None
    name_source: NameSource
    type: WaterBodyType
    # GeoJSON (Multi)LineString for a river or creek, Polygon for an outline
    # once one is sourced; None when no shape is on file. Simplified for map drawing.
    geometry: dict | None = None
    geometry_source: GeometrySource = "none"
    # (min_lon, min_lat, max_lon, max_lat) of the geometry; for a water with none,
    # of the spots that sit on it, so it can still be found by a map box.
    bbox: tuple[float, float, float, float] | None = None
    # The curated stretches that make up (or open onto) this water.
    stretch_ids: list[str] = Field(default_factory=list)
    # Facts that explain how the row was made: why it has this type, which
    # larger lake a bay belongs to.
    attributes: dict = Field(default_factory=dict)
    built_at: str

    @field_validator("water_body_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"water_body_id must be '<type>:<slug>', got {v!r}")
        return v

    @field_validator("name")
    @classmethod
    def _name_is_real(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip() or v.lstrip().lower().startswith("unnamed"):
            raise ValueError("an unknown name stays None; it is never filled with 'Unnamed ...'")
        return v

    @field_validator("geometry")
    @classmethod
    def _geometry_shape(cls, v: dict | None) -> dict | None:
        if v is not None and (
            v.get("type") not in ("LineString", "MultiLineString", "Polygon", "MultiPolygon")
            or not v.get("coordinates")
        ):
            raise ValueError("water body geometry must be a non-empty line or polygon")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if not self.water_body_id.startswith(f"{self.type}:"):
            raise ValueError(
                f"water_body_id {self.water_body_id!r} does not start with {self.type}"
            )
        if (self.name is None) != (self.name_source == "unknown"):
            raise ValueError("name is None exactly when name_source is 'unknown'")
        if (self.geometry is None) != (self.geometry_source == "none"):
            raise ValueError("geometry is None exactly when geometry_source is 'none'")
        return self
