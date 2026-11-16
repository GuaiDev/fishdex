"""Pydantic models for the scheduled area ingest.

The weekly ingest used to be a GitHub Action of hand-written curl steps, one
per (area, source) pair, POSTing to a hosted URL. The list of areas is now data
(each jurisdiction plugin's `cron_areas`, or a JSON file passed with `--areas`)
and the run happens locally through `src/services/area_ingest.py`.

`AreaIngestResult` keeps the per-dataset counts and failures instead of only
logging them, so the CLI can say which datasets landed and which did not — a
background task that logs and returns nothing reads the same whether it stored
five thousand rows or failed on every source.
"""

from pydantic import BaseModel, Field


class IngestArea(BaseModel):
    """One labelled area the weekly ingest covers."""

    label: str = Field(min_length=1)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
    """ISO 3166-2 code, e.g. CA-ON. Every location-bound record carries one."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0)
    sources: list[str] = Field(min_length=1)
    """Source names to run: "global", or a source the jurisdiction's plugin declares."""

    source_radius_km: dict[str, float] = Field(default_factory=dict)
    """Per-source radius override. Tidal stations are sparse, so a coastal area
    searches a wider radius for them than for observations."""

    def radius_for(self, source: str) -> float:
        return self.source_radius_km.get(source, self.radius_km)


class AreaIngestResult(BaseModel):
    """What one (area, source) ingest stored and what failed."""

    label: str
    source: str
    stored: dict[str, int] = Field(default_factory=dict)
    """Dataset name → rows stored (or row counts the adapter reported)."""

    failed: dict[str, str] = Field(default_factory=dict)
    """Dataset name → error, for every dataset that raised."""

    @property
    def ok(self) -> bool:
        return not self.failed
