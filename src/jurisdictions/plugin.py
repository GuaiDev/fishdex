"""The manifest a jurisdiction folder publishes about itself.

One `JurisdictionPlugin` per folder under `src/ingest/jurisdictions/<code>/`,
exported as `PLUGIN` from that folder's `plugin.py`. It replaces three
registries that each knew part of a region (the prompt registry, the ingest
config and the per-province service modules) and the hand-edited
bounding-box list and area file that sat beside them. The registry
(`src/jurisdictions/registry.py`) finds plugins by scanning the folders, so
adding a region means adding a folder and nothing else.

Everything here is declarative and cheap to import: runners are named by
dotted path and imported only when a source is actually run.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

GLOBAL_SOURCE = "global"
"""The worldwide bundle (iNaturalist, GBIF, WSC, OSM). Runs for any area, so it
belongs to no plugin."""

SourceStatus = Literal["implemented", "planned", "not_applicable"]


@dataclass(frozen=True)
class SourceSpec:
    """One dataset a jurisdiction can ingest.

    `runner` is "package.module:function" and is called with an `AreaContext`;
    it returns a row count or a dict of named row counts. A source with no
    runner is either not built yet, or is run by something other than the
    generic ingest (see `note`).
    """

    status: SourceStatus = "planned"
    runner: str | None = None
    area_scoped: bool = True
    """False for region-wide sources (regulations PDFs, species ranges): the
    area's lat/lng/radius are accepted and ignored."""
    label: str = ""
    """Name used for this dataset in ingest results. Defaults to the source name."""
    schedule: str | None = None
    """How often it should be refreshed, e.g. "weekly". Documentation for the
    cron job; `JurisdictionPlugin.areas` decides what `weekly-ingest` runs."""
    ttl_days: float | None = None
    """How long the adapter's cache serves a response before refetching."""
    ttl_constant: str | None = None
    """"package.module:NAME" of the adapter constant `ttl_days` mirrors (in
    seconds). A test compares the two, so the manifest cannot drift from the
    adapter."""
    licence: str | None = None
    url: str | None = None
    note: str = ""

    def load_runner(self) -> Callable[["AreaContext"], "int | dict[str, int]"]:
        if not self.runner:
            raise LookupError("source has no runner")
        module_name, _, func_name = self.runner.partition(":")
        return getattr(importlib.import_module(module_name), func_name)


@dataclass(frozen=True)
class AreaContext:
    """What a source runner is told about the area it is running for."""

    label: str
    lat: float
    lng: float
    radius_km: float


@dataclass(frozen=True)
class CronArea:
    """A labelled area `weekly-ingest` covers for this jurisdiction."""

    label: str
    lat: float
    lng: float
    radius_km: float
    sources: tuple[str, ...] = ("global",)
    """Source names to run, in order. "global" is the built-in worldwide
    bundle (iNaturalist, GBIF, WSC, OSM)."""
    source_radius_km: dict[str, float] = field(default_factory=dict)
    """Per-source radius override, e.g. tidal stations are sparse."""


@dataclass(frozen=True)
class BBox:
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float
    priority: int
    """Lookup order for coordinate tagging, lowest first; the first box that
    contains a point wins, so overlaps (Great Lakes border zones, Quebec's wide
    box swallowing the Maritimes) resolve by priority. Priorities are global
    across plugins; leave gaps when numbering."""

    def contains(self, lat: float, lng: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng


@dataclass(frozen=True)
class JurisdictionPlugin:
    code: str
    """ISO 3166-2, e.g. "CA-ON". Must match the folder: ca_on -> CA-ON."""
    name: str
    country: str
    bbox: BBox | None = None
    has_detailed_data: bool = False
    regulatory_context: str | None = None
    """Prompt text for this region. None falls back to the generic "no
    detailed data yet" paragraph."""
    sources: dict[str, SourceSpec] = field(default_factory=dict)
    cron_areas: tuple[CronArea, ...] = ()
    hydro_adapter: str | None = None
    """Dotted module of the stream-network adapter, e.g.
    "src.ingest.jurisdictions.ca_on.hydro_network"."""
    regulations_adapter: str | None = None
    status_authorities: tuple[str, ...] = ()
    """Bodies whose conservation listings apply here, e.g. COSEWIC, SARA, a
    provincial list. Declared now; the status schema that consumes them is a
    separate task."""
    curation: dict[str, str] = field(default_factory=dict)
    """Hand-curated files by role ("exclusions", "stretches"), as repo paths."""
    thresholds: dict[str, float] = field(default_factory=dict)
    notes: str = ""

    def source_status(self, name: str) -> bool | None:
        """True implemented, False planned, None not applicable, as the old
        `data_sources` dict reported it."""
        status = self.sources[name].status
        return {"implemented": True, "planned": False, "not_applicable": None}[status]
