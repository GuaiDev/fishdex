"""One feature read off a lake's shoreline: an inlet, an island, a point of land.

A shoreline feature is a located thing a person can see on the water — where a
creek enters, an island, a point — derived from a named, dated source and stored
against the water body it belongs to. It is not a fish claim: no field says what
lives near it. The words the map shows for a kind live in services/spot_labels.py.

`provenance` follows src/models/context.py: an island is a ring inside the OHN
polygon (a RECORD), while a point or peninsula is a shape *computed* from the
outline (INFERENCE). `derivation` says which came from the outline geometry
alone, so the map can label those "from the shoreline outline".
"""

from typing import Literal

from pydantic import BaseModel, field_validator

from src.models.context import ProvenanceKind
from src.models.jurisdiction import JurisdictionCode

FeatureKind = Literal["inlet", "outlet", "point", "peninsula", "island"]

# How the feature was obtained: read straight out of a source record, or worked
# out from the outline's shape.
Derivation = Literal["watercourse_endpoint", "shoreline_outline"]


class ShorelineFeature(BaseModel):
    """A mapped shoreline feature of one water body, with where it came from."""

    feature_id: str
    water_body_id: str
    jurisdiction: JurisdictionCode
    kind: FeatureKind
    name: str | None = None
    geometry: dict
    """GeoJSON Point (inlet, outlet, point, peninsula) or Polygon (island)."""
    source: str
    """'OHN Waterbody' or 'OHN Watercourse'."""
    source_date: str | None = None
    provenance: ProvenanceKind = ProvenanceKind.RECORD
    derivation: Derivation
    # The sentence a person reads under the feature. Built at render time from
    # the labels vocabulary (services/spot_labels.py), never stored free text.
    note: str | None = None
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
        if v.get("type") not in ("Point", "Polygon"):
            raise ValueError("shoreline feature geometry must be a Point or a Polygon")
        if not v.get("coordinates"):
            raise ValueError("shoreline feature geometry must not be empty")
        return v
