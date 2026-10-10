from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WaterFeature(BaseModel):
    osm_id: str  # e.g. "way/12345"
    feature_type: Literal[
        "lake",
        "river",
        "stream",
        "pond",
        "reservoir",
        "wetland",
        "canal",
        "ditch",
        "drain",
        "bay",
    ]
    name: str | None
    lat: float
    lng: float
    jurisdiction: str
    area_m2: float | None
    tags: dict
    fetched_at: datetime = Field(default_factory=datetime.now)


class AccessPoint(BaseModel):
    osm_id: str
    access_type: Literal[
        "boat_launch",
        "parking",
        "trail_head",
        "fishing_spot",
        "public_land",
        "conservation_area",
        "park",
        "road",
        "building",
    ]
    name: str | None
    lat: float
    lng: float
    jurisdiction: str
    tags: dict
    fetched_at: datetime = Field(default_factory=datetime.now)


class CorridorFeature(BaseModel):
    """An OSM feature found beside a river: a dam, somewhere to park, a road over it.

    Raw candidate for the explore map's stretch anchors. Whether it becomes an
    anchor — close enough to the water, open to the public — is decided by
    the caller, which knows where the water is.
    """

    osm_id: str
    kind: Literal[
        "dam", "weir", "lock", "parking", "trail_head", "boat_launch", "fishing_spot", "road"
    ]
    name: str | None
    lat: float
    lng: float
    jurisdiction: str
    tags: dict
    # (lon, lat) vertices — set for roads only, which are lines the caller
    # intersects with the river rather than points.
    line: list[tuple[float, float]] | None = None
