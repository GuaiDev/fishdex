"""Pydantic models for the coverage check.

The coverage check compares the map against authoritative reference layers and
says, for every reference feature, why it is or is not on the map. Nothing in
these models is specific to a region: a region is a registry file plus a
jurisdiction code, and the registry decides what the reference layers are.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.models.jurisdiction import JurisdictionCode


class Cause(StrEnum):
    """Where a reference feature stopped being on the map: one rung of the ladder.

    The order is the ladder's order. Each rung has a different remedy, so a list
    of misses reads as a work list.
    """

    MATCHED = "matched"
    MISSING_CURATION = "missing_curation"  # no curated water near it: a product choice
    MISSING_DATA = "missing_data"  # on curated water, no anchor: fix the ingest
    DETECTION = "detection"  # anchor, no spot: fix the spot builder
    PLACEMENT = "placement"  # spot, but in the wrong place (S2b)
    DEPLOY = "deploy"  # spot in the build, not on the host
    FILTER = "filter"  # served, but not at the demo's tier or zoom (S2b)
    RENDERING = "rendering"  # needs a browser (not checked here)


class ReferenceFeature(BaseModel):
    """One feature from an authoritative reference layer, reduced to a point.

    Point classes (dams, access points) are stored as they are. A line or polygon
    class would carry its geometry in `geometry`; S2a only needs points.
    """

    ref_id: str
    name: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    attributes: dict = Field(default_factory=dict)


class ReferenceSpec(BaseModel):
    """One reference layer for one feature class, as the registry describes it."""

    feature_class: str  # "dams", later "lakes", "access", "mouths"
    label: str
    service_url: str  # an ArcGIS REST layer: .../MapServer/0 or .../FeatureServer/0
    id_field: str
    name_field: str | None = None
    where: str = "1=1"
    licence: str = ""
    # False when the reference is also a map source: matching it then measures
    # self-agreement, not coverage (report 1.6).
    independent: bool = True

    # The ladder's radii, in metres.
    scope_m: float = Field(gt=0)  # within this of a curated stretch line is "in scope"
    anchor_m: float = Field(gt=0)  # an anchor this close counts as the map having it
    spot_m: float = Field(gt=0)  # a spot this close to the anchor or reference counts
    anchor_types: list[str]
    spot_kinds: list[str]

    # The focus-area gate: recall at or above this passes. None means report only.
    min_recall: float | None = Field(default=None, ge=0, le=1)


class FocusArea(BaseModel):
    name: str = ""  # filled from the registry key
    bbox: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat

    @model_validator(mode="after")
    def _ordered(self):
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError(f"focus {self.name!r}: bbox must be min_lon,min_lat,max_lon,max_lat")
        return self


class ParitySpec(BaseModel):
    """The tables the map reads, which the host must hold as many rows of as the build."""

    tables: list[str]


class ListingSpec(BaseModel):
    """The table the protection gate reads. Empty for the jurisdiction means fail open."""

    table: str = "species_ranges"


class LivenessSpec(BaseModel):
    path: str = "/health"
    min_vantage_points: int = Field(default=3, ge=1)


class CoverageRegistry(BaseModel):
    """A jurisdiction's reference registry, loaded from `data/coverage/references_*.toml`."""

    version: int = 1
    jurisdiction: JurisdictionCode
    focus: dict[str, FocusArea]
    references: list[ReferenceSpec]
    parity: ParitySpec
    listing: ListingSpec = Field(default_factory=ListingSpec)
    liveness: LivenessSpec = Field(default_factory=LivenessSpec)

    @model_validator(mode="after")
    def _name_focus_areas(self):
        for key, area in self.focus.items():
            area.name = key
        return self


# ── what a database (the build's, or the host's) holds ───────────────────────


class ProbeSpot(BaseModel):
    spot_id: str
    kind: str
    lat: float
    lon: float


class ListingProbe(BaseModel):
    table: str
    table_present: bool
    rows_for_jurisdiction: int = 0
    rows_total: int = 0


class DbProbe(BaseModel):
    """The facts the check needs from one database.

    The build is probed from its file; the host is probed from a copy of its file
    or, for `--api`, by the host itself (`GET /admin/coverage-probe`). Both produce
    this one shape, so the comparison never depends on how a side was read.
    """

    source: str
    # Row count per table; None means the table does not exist there.
    table_rows: dict[str, int | None]
    listing: ListingProbe
    spots: list[ProbeSpot] = Field(default_factory=list)


# ── results ─────────────────────────────────────────────────────────────────


class FeatureResult(BaseModel):
    """What happened to one reference feature on the ladder."""

    ref_id: str
    name: str | None
    lat: float
    lon: float
    cause: Cause
    # Metres to the nearest curated stretch line, and to the anchor or spot the
    # ladder stopped at. None where that step was never reached.
    stretch_distance_m: float | None = None
    anchor_distance_m: float | None = None
    spot_distance_m: float | None = None
    stretch_id: str | None = None
    detail: str = ""


class ClassReport(BaseModel):
    """One feature class: the reference count, what was filtered, and the ladder tally."""

    feature_class: str
    label: str
    independent: bool
    reference_total: int  # everything the reference returned for the focus area
    in_scope: int  # within scope_m of curated water
    out_of_scope: int  # filtered out: the report says how many, never hides them
    matched: int  # in-scope features that reached the map
    recall: float | None  # matched / in_scope; None when nothing was in scope
    min_recall: float | None
    by_cause: dict[str, int]
    results: list[FeatureResult] = Field(default_factory=list)
    # Why the class could not be measured at all (e.g. no stretches stored).
    unmeasured_reason: str | None = None

    @property
    def passes(self) -> bool | None:
        """None when the class has no gate or could not be measured."""
        if self.min_recall is None or self.unmeasured_reason or self.recall is None:
            return None
        return self.recall >= self.min_recall


class ParityRow(BaseModel):
    table: str
    build_rows: int | None  # None: table absent from the build
    hosted_rows: int | None  # None: table absent from (or unknown on) the host

    @property
    def ok(self) -> bool:
        """100% parity. A table the build does not have cannot fail parity."""
        if not self.build_rows:
            return True
        return (self.hosted_rows or 0) >= self.build_rows


class ParityReport(BaseModel):
    source: str  # what the hosted counts came from: a file path or an API URL
    rows: list[ParityRow]

    @property
    def failures(self) -> list[ParityRow]:
        return [r for r in self.rows if not r.ok]


class ListingReport(BaseModel):
    """The listing-table invariant: the gate needs status data to be able to refuse."""

    table: str
    jurisdiction: JurisdictionCode
    rows_for_jurisdiction: int
    rows_total: int
    table_present: bool
    # Species named on cards that the table lists as protected. None: not measured
    # (it needs the API's cards).
    leak_count: int | None = None

    @property
    def ok(self) -> bool:
        return self.rows_for_jurisdiction > 0 and not self.leak_count


class VantageResult(BaseModel):
    node: str
    ok: bool
    status: int | None = None
    detail: str = ""


class LivenessReport(BaseModel):
    url: str
    required: int
    vantage_points: list[VantageResult]
    # Where the probes came from, so a report never claims "3 of 3 outside" for a
    # check that ran from one machine.
    prober: str

    @property
    def ok_count(self) -> int:
        return sum(1 for v in self.vantage_points if v.ok)

    @property
    def ok(self) -> bool:
        return self.ok_count >= self.required


GateKind = Literal["hard", "focus"]


class GateResult(BaseModel):
    name: str
    kind: GateKind
    passed: bool
    measured: str  # what was found, in words a reader can check
    threshold: str
    skipped: bool = False  # not run in this mode (e.g. liveness without --api)


class CoverageReport(BaseModel):
    jurisdiction: JurisdictionCode
    focus: str
    db_path: str
    generated_at: str
    classes: list[ClassReport] = Field(default_factory=list)
    parity: ParityReport | None = None
    listing: ListingReport | None = None
    liveness: LivenessReport | None = None
    gates: list[GateResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def hard_failures(self) -> list[GateResult]:
        return [g for g in self.gates if g.kind == "hard" and not g.passed and not g.skipped]

    @property
    def focus_failures(self) -> list[GateResult]:
        return [g for g in self.gates if g.kind == "focus" and not g.passed and not g.skipped]

    @property
    def exit_code(self) -> int:
        """Non-zero when a hard gate failed. Focus gates inform; they do not block."""
        return 1 if self.hard_failures else 0
