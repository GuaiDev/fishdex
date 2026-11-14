"""Pydantic model for one stored stream segment in the on-disk hydro store.

The map build used to hold a whole region's stream network in one process
(~5.7 KB per segment). The `hydro_segments` table is that network on disk, with
an R*Tree over each segment's bounding box so a build can load one stretch's
neighbourhood instead of the region.

A record is a located line and its topology, nothing about fish. Ids are keyed by
`(segment_source, seg_id)` because two sources can publish the same native id;
`jurisdiction` says whose water it is, and nothing in the model is tied to one
province or state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from shapely import wkb, wkt

from src.models.jurisdiction import JurisdictionCode
from src.services.hydro_nodes import encode_node, encode_node_str


class HydroSegmentRecord(BaseModel):
    jurisdiction: JurisdictionCode
    segment_source: str = Field(min_length=1)  # "OHN", "FWA", "NHDPlusHR", ...
    seg_id: int  # the source's own id; unique only within segment_source
    start_node: int  # services.hydro_nodes.encode_node of the first vertex
    end_node: int  # ... of the last vertex
    length_m: float = Field(ge=0)
    name: str | None = None
    flow_verified: bool
    # True for a centreline drawn through a lake or reservoir rather than a channel.
    is_waterbody_path: bool = False
    # Upstream channel km, None until accumulated (or until the source supplies it).
    upstream_km: float | None = Field(default=None, ge=0)
    # The ingest tile this came from, so a tile can be replaced as a unit.
    tile_key: str | None = None
    geom: bytes  # WKB LineString or MultiLineString, coordinates (lon, lat)
    # Bounding box of `geom`; feeds the R*Tree. `from_wkt` derives it.
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _bbox_is_ordered(self) -> HydroSegmentRecord:
        if self.min_lon > self.max_lon or self.min_lat > self.max_lat:
            raise ValueError("bounding box minimum exceeds maximum")
        return self

    @classmethod
    def from_wkt(
        cls,
        *,
        geom_wkt: str,
        start_node: str | None = None,
        end_node: str | None = None,
        **fields,
    ) -> HydroSegmentRecord:
        """Build a record from a WKT line, deriving WKB, bbox and (if not given) node ids.

        `start_node` / `end_node` accept the legacy `"lon,lat"` strings; left out,
        they are taken from the geometry's first and last vertex. A MultiLineString
        has no single first/last vertex here, so the caller must pass them.
        """
        geom = wkt.loads(geom_wkt)
        if geom.geom_type not in ("LineString", "MultiLineString"):
            raise ValueError(f"expected a line geometry, got {geom.geom_type}")
        if geom.is_empty:
            raise ValueError("empty geometry")
        if start_node is not None and end_node is not None:
            start, end = encode_node_str(start_node), encode_node_str(end_node)
        elif geom.geom_type == "LineString":
            start = encode_node(*geom.coords[0][:2])
            end = encode_node(*geom.coords[-1][:2])
        else:
            raise ValueError("a MultiLineString needs explicit start_node and end_node")
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        return cls(
            start_node=start,
            end_node=end,
            geom=wkb.dumps(geom),
            min_lon=min_lon,
            max_lon=max_lon,
            min_lat=min_lat,
            max_lat=max_lat,
            **fields,
        )
