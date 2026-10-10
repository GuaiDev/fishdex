"""Pydantic models for Level 2 of the explore map: anchors inside a stretch.

An anchor is a feature worth tapping once the map has zoomed into a stretch:
a confluence, a dam and the tailwater below it, the lake mouth, a place to
park or cross the water. Each belongs to one stretch and says where it came
from — the OHN network's topology, or OpenStreetMap — so a later step that
explains an anchor can tell a surveyed fact from a derived one.

Anchors are located features, not fish predictions. Nothing here says a
confluence holds fish; it says two channels meet there.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.models.jurisdiction import JurisdictionCode
from src.models.stretch import StretchId

AnchorType = Literal["confluence", "dam", "tailwater", "lake_mouth", "access_point"]

# What kind of dam-like structure, or what kind of access, an anchor is.
# confluence, tailwater and lake_mouth carry no subtype.
DamKind = Literal["dam", "weir", "lock"]
AccessKind = Literal["parking", "trail_head", "boat_launch", "fishing_spot", "road_crossing"]

AnchorSource = Literal["ohn_topology", "osm"]


class StretchAnchor(BaseModel):
    """A located feature inside a stretch, as stored in stretch_anchors."""

    # Stable across rebuilds: built from the stretch id and a key that comes
    # from the feature itself (OHN node coordinates, OSM element id), never
    # from list position — a saved pin must still point at the same place
    # after the stretches are rebuilt.
    anchor_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:_.-]*$")
    stretch_id: StretchId
    jurisdiction: JurisdictionCode
    anchor_type: AnchorType
    subtype: str | None = None
    name: str | None = None  # only where a source gives one; never invented
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source: AnchorSource
    source_ref: str  # "ogf:123,456" (OHN segments) or "way/123" (OSM element)
    # Nearest segment of the stretch and how far the feature sits from it.
    ogf_id: int | None = None
    snap_distance_m: float | None = Field(default=None, ge=0)
    # Type-specific facts: a confluence's tributary and its size, a dam's
    # tailwater reach, an access point's OSM tags worth keeping. JSON-safe.
    attributes: dict = Field(default_factory=dict)
    built_at: str


class StretchSegmentLine(BaseModel):
    """One OHN segment of a stretch, drawn at full detail for the zoomed view.

    The stretch's own geometry is simplified for province zoom; these are what
    the map draws once the stretch fills the screen.
    """

    ogf_id: int
    stretch_id: StretchId
    jurisdiction: JurisdictionCode
    name: str | None = None
    geometry: dict  # GeoJSON LineString


# ── build diagnostics ─────────────────────────────────────────────────────────

AnchorIssueKind = Literal[
    "osm_unavailable",  # Overpass failed for a stretch; OSM anchors carried over or absent
    "tailwater_unverified",  # a dam sits on water whose flow direction OHN has not verified
]


class AnchorBuildIssue(BaseModel):
    stretch_id: StretchId
    kind: AnchorIssueKind
    detail: str


class AnchorBuildReport(BaseModel):
    jurisdiction: JurisdictionCode
    stretches: int
    anchors_total: int = 0
    by_stretch: dict[StretchId, dict[str, int]] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    # Candidates seen and deliberately not published, by reason. A count that
    # separates "nothing there" from "found and filtered" belongs in the return
    # value, not in a log nobody reads.
    dropped: dict[str, int] = Field(default_factory=dict)
    issues: list[AnchorBuildIssue] = Field(default_factory=list)
    carried_over_stretches: list[StretchId] = Field(default_factory=list)

    def drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + n

    @property
    def stretches_without_osm(self) -> list[StretchId]:
        return sorted({i.stretch_id for i in self.issues if i.kind == "osm_unavailable"})
