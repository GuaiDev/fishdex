"""Pydantic models for Ontario Hydro Network stream connectivity data."""

from pydantic import BaseModel


class StreamSegment(BaseModel):
    ogf_id: int
    watercourse_type: str  # "Stream" | "Virtual Flow"
    name: str | None = None  # OFFICIAL_NAME_LABEL — most segments unnamed
    flow_verified: bool  # FLOW_DIRECTION_VERIFIED_IND == "Yes"
    permanency: str  # "Permanent" | seasonal
    flow_classification: str | None = None
    stream_order: int | None = None  # Strahler stream order (FWA: STREAM_ORDER)
    length_m: float
    effective_date: str | None = None  # ISO date the source record took effect
    geom_wkt: str  # WKT LineString or MultiLineString, coords as (lon lat)
    start_node: str  # "lon,lat" rounded to 5 decimal places
    end_node: str  # "lon,lat" rounded to 5 decimal places
    jurisdiction: str = "CA-ON"
    segment_source: str = "OHN"  # source system: "OHN" (Ontario) or "FWA" (BC)


class HydroBarrier(BaseModel):
    ogf_id: int
    barrier_type: str  # "Falls" | "Rapids" | "Rocks" | "Sea Lamprey Barrier"
    geom_wkt: str  # WKT Point
    nearest_segment_ogf_id: int | None = None
    snap_distance_m: float | None = None
    jurisdiction: str = "CA-ON"


class ConnectivityResult(BaseModel):
    query_lat: float
    query_lon: float
    species: str | None
    connected_observations: list[dict]
    nearest_barrier: str | None  # barrier_type of the first barrier on path, if any
    summary_sentence: str
