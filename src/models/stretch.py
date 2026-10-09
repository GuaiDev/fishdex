"""Pydantic models for Level 1 of the explore map: named fishing stretches.

A stretch is a named run of river ("Dunnville to Port Maitland", "Lower
Credit") drawn at low zoom and tapped to zoom in. Three kinds of record live
here:

- the curation file's schema (CuratedStretch and friends) — what a person
  edits to rename, merge, split or add a stretch without touching code;
- the published stretch and its segment membership (FishingStretch,
  StretchSegment) — what the map reads;
- the build's account of itself (CandidateStretch, StretchBuildIssue,
  StretchBuildReport) — what clustering proposed and what failed, so a
  stretch that silently did not build is never mistaken for one that did.
"""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.jurisdiction import JurisdictionCode

StretchId = str
_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


# ── curation file ─────────────────────────────────────────────────────────────


class BoundaryPoint(BaseModel):
    """A point on the river that bounds or steers a reach. (lat, lon), WGS84."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str | None = None  # "Dunnville dam" — for the reader of the file
    # Pin to this OHN segment instead of snapping — the override for a point
    # the snap rule (largest channel within max_snap_m) gets wrong.
    ogf_id: int | None = None


class CuratedReach(BaseModel):
    """One continuous run of channel, from one boundary point to another.

    The build snaps each point to the largest channel within the stretch's
    snap radius and follows the stream network between them. `via` points
    pin the route when two channels connect more than one way (a braid, a
    river through a lake).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: BoundaryPoint = Field(alias="from")
    to: BoundaryPoint
    via: list[BoundaryPoint] = Field(default_factory=list)

    def waypoints(self) -> list[BoundaryPoint]:
        return [self.from_, *self.via, self.to]


class CuratedStretch(BaseModel):
    """One published stretch, as written in the curation file.

    Several reaches in one stretch is a merge; one river cut into two entries
    that share a boundary point is a split.
    """

    model_config = ConfigDict(extra="forbid")

    id: StretchId = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1)
    river: str = Field(min_length=1)
    region: str | None = None
    jurisdiction: JurisdictionCode | None = None  # falls back to the file's
    reaches: list[CuratedReach] = Field(alias="reach", min_length=1)
    include_ogf_ids: list[int] = Field(default_factory=list)
    exclude_ogf_ids: list[int] = Field(default_factory=list)
    max_snap_m: float = Field(default=400.0, gt=0, le=5000)
    enabled: bool = True
    notes: str | None = None  # where and why curation was needed


class StretchCuration(BaseModel):
    """The whole curation file."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    jurisdiction: JurisdictionCode
    # (min_lon, min_lat, max_lon, max_lat): the network fetched for the build.
    # Candidate clustering runs over all of it; curated points must fall inside.
    build_bbox: tuple[float, float, float, float]
    stretches: list[CuratedStretch] = Field(alias="stretch", default_factory=list)

    @field_validator("build_bbox")
    @classmethod
    def _bbox_ordered(cls, v: tuple[float, float, float, float]):
        min_lon, min_lat, max_lon, max_lat = v
        if not (min_lon < max_lon and min_lat < max_lat):
            raise ValueError("build_bbox must be (min_lon, min_lat, max_lon, max_lat)")
        return v

    @model_validator(mode="after")
    def _ids_unique(self):
        counts = Counter(s.id for s in self.stretches)
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate stretch ids: {', '.join(dupes)}")
        return self

    def jurisdiction_of(self, stretch: CuratedStretch) -> str:
        return stretch.jurisdiction or self.jurisdiction


# ── published records ─────────────────────────────────────────────────────────


class FishingStretch(BaseModel):
    """A built stretch, as stored in fishing_stretches and served to the map."""

    stretch_id: StretchId = Field(pattern=_ID_PATTERN)
    name: str
    river: str
    region: str | None = None
    jurisdiction: JurisdictionCode
    length_km: float = Field(ge=0)
    segment_count: int = Field(ge=1)
    # GeoJSON LineString or MultiLineString, simplified for low-zoom drawing
    geometry: dict
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    centroid_lat: float
    centroid_lon: float
    sort_order: int = 0  # position in the curation file
    notes: str | None = None
    source: str = "OHN"
    built_at: str

    @field_validator("geometry")
    @classmethod
    def _line_geometry(cls, v: dict):
        if v.get("type") not in ("LineString", "MultiLineString") or not v.get("coordinates"):
            raise ValueError("stretch geometry must be a non-empty (Multi)LineString")
        return v


class StretchSegment(BaseModel):
    """Membership row: one OHN segment belongs to at most one stretch."""

    ogf_id: int
    stretch_id: StretchId
    jurisdiction: JurisdictionCode
    seq: int = Field(ge=0)  # order along the traced reach, downstream-agnostic


# ── build diagnostics ─────────────────────────────────────────────────────────


class CandidateStretch(BaseModel):
    """A run of major channel the clustering proposes as a stretch.

    Candidates are not published. They are what the curation file is checked
    against: one that no curated stretch covers is a river the map is missing,
    and the report prints it as a ready-to-paste curation entry.
    """

    candidate_id: str
    suggested_name: str | None = None
    jurisdiction: JurisdictionCode
    length_km: float
    upstream_km: float  # channel length draining into its downstream end
    downstream: BoundaryPoint
    upstream: BoundaryPoint
    ogf_ids: list[int]
    covered_fraction: float = 0.0  # share of length inside curated stretches


IssueKind = Literal[
    "snap_failed",  # a boundary point is not within max_snap_m of any channel
    "outside_build_area",  # a boundary point lies outside build_bbox
    "no_path",  # snapped points are on networks that do not connect
    "suspicious_path",  # traced length far exceeds the straight-line distance
    "segment_conflict",  # segments already claimed by an earlier stretch
    "empty_stretch",  # overrides and conflicts left no segments to publish
    "unknown_ogf_id",  # include_ogf_ids names a segment not in the network
]

# Kinds that stop a stretch from being published; the rest are warnings.
BLOCKING_ISSUES: frozenset[str] = frozenset(
    {"snap_failed", "outside_build_area", "no_path", "empty_stretch"},
)


class StretchBuildIssue(BaseModel):
    stretch_id: StretchId
    kind: IssueKind
    detail: str

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_ISSUES


class StretchBuildReport(BaseModel):
    jurisdiction: JurisdictionCode
    network_segments: int
    curated_total: int
    built: list[StretchId] = Field(default_factory=list)
    disabled: list[StretchId] = Field(default_factory=list)
    issues: list[StretchBuildIssue] = Field(default_factory=list)
    segments_assigned: int = 0
    candidates_total: int = 0
    uncovered_candidates: list[CandidateStretch] = Field(default_factory=list)

    @property
    def failed(self) -> list[StretchId]:
        return sorted({i.stretch_id for i in self.issues if i.blocking})
