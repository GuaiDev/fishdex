"""Per-place species counts: what has been recorded in one grid cell, aggregated.

Not raw records. The angler profile only needs to know which species are
recorded around a place and how much recording there is, so the counts are
pulled as aggregates (iNaturalist species_counts, GBIF faceted counts) — which
also keeps the pull clear of iNaturalist's 10,000-record pagination cap.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class PlaceSpeciesCount(BaseModel):
    source: str
    """"iNaturalist" (research grade only) or "GBIF"."""

    cell_id: str
    south: float
    west: float
    north: float
    east: float
    jurisdiction: str
    scientific_name: str
    common_name: str | None = None
    count: int
    fetched_at: datetime = Field(default_factory=datetime.now)
