"""One feature read off a lake's shoreline or its surveyed bottom.

A shoreline feature is a located thing a person can see on the water — where a
creek enters, an island, a point — derived from a named, dated source and stored
against the water body it belongs to. It is not a fish claim: no field says what
lives near it. The words the map shows for a kind live in services/spot_labels.py.

Kinds come from the outline (inlet, outlet, island, point, peninsula), from a
lake's surveyed depth contours (drop_off, hump) and from OpenStreetMap (pier,
breakwater, harbour). `provenance` follows src/models/context.py: an island is a
ring inside the OHN polygon and an inlet is a mapped stream (both RECORD), while a
point, peninsula, drop-off or hump is a shape *computed* from the geometry
(INFERENCE). `derivation` says where the shape came from, so the map can say "from
the shoreline outline" or "from a 1970 survey, transect".

A pier, breakwater or harbour is a different sort of thing: it is a mapped
structure read straight from OpenStreetMap (`derivation == "osm_feature"`), not
computed from any outline. It carries its OSM element id and the source name,
and — like every feature here — says nothing about fish.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.models.context import ProvenanceKind
from src.models.jurisdiction import JurisdictionCode

# A 1 m to 3 m change is a gentle slope, not a place the bottom "falls away quickly",
# so it is not a drop-off. Applied at build time and again when rows are read.
DROP_OFF_MIN_DEPTH_CHANGE_M = 3.0

FeatureKind = Literal[
    "inlet",
    "outlet",
    "point",
    "peninsula",
    "island",
    "drop_off",
    "hump",
    "pier",
    "breakwater",
    "harbour",
]

# How the feature was obtained: read straight out of a source record, worked out
# from the outline's shape, computed from a lake's surveyed depth contours, or a
# mapped structure read from OpenStreetMap.
Derivation = Literal[
    "watercourse_endpoint", "shoreline_outline", "bathymetry_contours", "osm_feature"
]


class ShorelineFeature(BaseModel):
    """A mapped shoreline feature of one water body, with where it came from."""

    feature_id: str
    water_body_id: str
    jurisdiction: JurisdictionCode
    kind: FeatureKind
    name: str | None = None
    geometry: dict
    """GeoJSON Point (inlet, outlet, point, peninsula, drop_off), Polygon (island, hump,
    harbour) or LineString (a pier or breakwater drawn as a line)."""
    source: str
    """'OHN Waterbody', 'OHN Watercourse', 'LIO Bathymetry' or 'OpenStreetMap'."""
    source_date: str | None = None
    provenance: ProvenanceKind = ProvenanceKind.RECORD
    derivation: Derivation
    # The sentence a person reads under the feature. Built at render time from
    # the labels vocabulary (services/spot_labels.py), never stored free text.
    note: str | None = None
    # Source facts a derivation used, kept so a feature can say what it was read
    # from: the contour OGF_IDs, the survey year and method, the depth. Empty for
    # features read off an outline.
    attributes: dict = Field(default_factory=dict)
    area_m2: float | None = None
    length_m: float | None = None
    built_at: str

    @field_validator("feature_id")
    @classmethod
    def _id_has_kind(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError(f"feature_id must be '<water_body_id>:<kind>:<n>', got {v!r}")
        return v

    @field_validator("geometry")
    @classmethod
    def _geometry_shape(cls, v: dict) -> dict:
        if v.get("type") not in ("Point", "Polygon", "LineString"):
            raise ValueError("shoreline feature geometry must be a Point, Polygon or LineString")
        if not v.get("coordinates"):
            raise ValueError("shoreline feature geometry must not be empty")
        return v
