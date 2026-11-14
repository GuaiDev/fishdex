"""Int64 encoding of stream-segment endpoints, for the on-disk hydro store.

A segment's topology is "this one ends where that one starts". The in-memory
network keys that on a `"lon,lat"` string rounded to five decimal places
(about 1 m). The store keeps the same rounding but packs the pair into one
integer, so an endpoint is 8 bytes instead of a ~20-byte string, two segments
join with an integer compare, and nothing needs a global id-to-id mapping:
the same point encodes to the same number whichever source or tile it came from.

    node = ((lon_e5 + 18_000_000) << 25) | (lat_e5 + 9_000_000)

`lon_e5` / `lat_e5` are degrees times 1e5, rounded. The offsets make both parts
non-negative; latitude needs 25 bits (0..18_000_000 < 2**25) and the longitude
part sits above it, so the whole value is under 2**51 and fits a signed int64.
Nothing here is specific to a region: it works on both sides of the equator and
the antimeridian-free range of -180..180.
"""

from __future__ import annotations

_SCALE = 100_000  # 1e-5 degrees, the rounding the in-memory network already uses
_LON_OFFSET = 18_000_000
_LAT_OFFSET = 9_000_000
_LAT_BITS = 25
_LAT_MASK = (1 << _LAT_BITS) - 1

_LON_E5_MAX = 18_000_000
_LAT_E5_MAX = 9_000_000


def encode_node(lon: float, lat: float) -> int:
    """Pack a (lon, lat) in degrees into the store's int64 node id.

    Raises ValueError for a coordinate outside -180..180 / -90..90, or NaN:
    an out-of-range value would otherwise bleed into the neighbouring bit
    field and silently name a different place.
    """
    lon_e5 = round(lon * _SCALE) if lon == lon else None  # NaN != NaN
    lat_e5 = round(lat * _SCALE) if lat == lat else None
    if (
        lon_e5 is None
        or lat_e5 is None
        or not -_LON_E5_MAX <= lon_e5 <= _LON_E5_MAX
        or not -_LAT_E5_MAX <= lat_e5 <= _LAT_E5_MAX
    ):
        raise ValueError(f"coordinate out of range for a node: lon={lon!r} lat={lat!r}")
    return ((lon_e5 + _LON_OFFSET) << _LAT_BITS) | (lat_e5 + _LAT_OFFSET)


def decode_node(node: int) -> tuple[float, float]:
    """Unpack a node id back to (lon, lat) in degrees, at 1e-5 resolution."""
    if node < 0:
        raise ValueError(f"not a node id: {node!r}")
    lon_e5 = (node >> _LAT_BITS) - _LON_OFFSET
    lat_e5 = (node & _LAT_MASK) - _LAT_OFFSET
    if not -_LON_E5_MAX <= lon_e5 <= _LON_E5_MAX or not -_LAT_E5_MAX <= lat_e5 <= _LAT_E5_MAX:
        raise ValueError(f"not a node id: {node!r}")
    return lon_e5 / _SCALE, lat_e5 / _SCALE


def encode_node_str(node: str) -> int:
    """Encode the legacy `"lon,lat"` string (`StreamSegment.start_node`) as a node id."""
    try:
        lon_s, lat_s = node.split(",")
        return encode_node(float(lon_s), float(lat_s))
    except ValueError as exc:
        raise ValueError(f"not a 'lon,lat' node string: {node!r}") from exc
