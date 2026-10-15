"""Waters the app does not suggest: First Nations territory it has no boundary layer for.

CLAUDE.md: Indigenous / First Nations waters are flagged as a separate
jurisdiction and nothing is predicted within them. The stretch curation file
already leaves those reaches out of the map. Anything that ranks water on its
own, like quiet water (an `explore()` wrapper), has to be told the same, and no
boundary layer exists in this repository, so the zones are written down by hand
as bounding boxes in a file beside the stretch curation.

A box is a deliberately generous stand-in: it is drawn around the omitted reach
with padding, so it errs towards leaving good water out rather than suggesting
water that should not be suggested. Replace it with the official boundary when
a layer is ingested.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.jurisdiction import JurisdictionCode


class ExclusionZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    # (min_lon, min_lat, max_lon, max_lat)
    bbox: tuple[float, float, float, float]
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered(self):
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if not (min_lon < max_lon and min_lat < max_lat):
            raise ValueError("bbox must be (min_lon, min_lat, max_lon, max_lat)")
        return self

    def contains(self, lat: float, lng: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lon <= lng <= max_lon and min_lat <= lat <= max_lat


class ExclusionZoneFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    jurisdiction: JurisdictionCode
    zones: list[ExclusionZone] = Field(alias="zone", default_factory=list)
