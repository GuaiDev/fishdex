"""OpenStreetMap water features and access points via Overpass API.

Cache TTL: 30 days — water bodies and access infrastructure change rarely.
Rate limit: 0.5s sleep before every live request (Overpass polite use policy).
"""

import hashlib
import json
import logging
import math
import time
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.water_feature import AccessPoint, CorridorFeature, ShoreStructure, WaterFeature

logger = logging.getLogger(__name__)

# Primary + community mirror — tried in order on each attempt
_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_RETRY_DELAYS = [5, 15, 30]  # seconds between attempts; 4 total (initial + 3 retries)
_CACHE_DIR = Path("data/cache/osm")
_CACHE_TTL_SECONDS = 2_592_000  # 30 days
_USER_AGENT = "fishbot/1.0 (personal fishing assistant)"


def fetch_water_features(lat: float, lng: float, radius_km: float = 25) -> list[WaterFeature]:
    """Fetch water bodies near lat/lng within radius_km. Cached 30 days."""
    radius_m = int(radius_km * 1000)
    query = (
        f"[out:json][timeout:60];\n"
        f"(\n"
        f'  node["natural"="water"](around:{radius_m},{lat},{lng});\n'
        f'  way["natural"="water"](around:{radius_m},{lat},{lng});\n'
        f'  node["waterway"~"^(river|stream|canal|drain|ditch)$"](around:{radius_m},{lat},{lng});\n'
        f'  way["waterway"~"^(river|stream|canal|drain|ditch)$"](around:{radius_m},{lat},{lng});\n'
        f'  node["natural"="wetland"](around:{radius_m},{lat},{lng});\n'
        f'  way["natural"="wetland"](around:{radius_m},{lat},{lng});\n'
        f'  node["natural"="bay"](around:{radius_m},{lat},{lng});\n'
        f'  way["natural"="bay"](around:{radius_m},{lat},{lng});\n'
        f");\n"
        f"out geom;"
    )
    data = _overpass_query(query)
    features = []
    for element in data.get("elements", []):
        feature = _parse_water_feature(element)
        if feature is not None:
            features.append(feature)
    return features


def fetch_access_points(lat: float, lng: float, radius_km: float = 25) -> list[AccessPoint]:
    """Fetch access points near lat/lng within radius_km. Cached 30 days."""
    radius_m = int(radius_km * 1000)
    query = (
        f"[out:json][timeout:90];\n"
        f"(\n"
        f'  node["leisure"="fishing"](around:{radius_m},{lat},{lng});\n'
        f'  way["leisure"="fishing"](around:{radius_m},{lat},{lng});\n'
        f'  node["amenity"="boat_ramp"](around:{radius_m},{lat},{lng});\n'
        f'  way["amenity"="boat_ramp"](around:{radius_m},{lat},{lng});\n'
        f'  node["highway"="trailhead"](around:{radius_m},{lat},{lng});\n'
        f'  way["highway"="trailhead"](around:{radius_m},{lat},{lng});\n'
        f'  node["leisure"="nature_reserve"](around:{radius_m},{lat},{lng});\n'
        f'  way["leisure"="nature_reserve"](around:{radius_m},{lat},{lng});\n'
        f'  node["boundary"="protected_area"](around:{radius_m},{lat},{lng});\n'
        f'  way["boundary"="protected_area"](around:{radius_m},{lat},{lng});\n'
        f'  node["leisure"="park"](around:{radius_m},{lat},{lng});\n'
        f'  way["leisure"="park"](around:{radius_m},{lat},{lng});\n'
        f'  node["highway"="layby"](around:{radius_m},{lat},{lng});\n'
        f'  way["highway"="layby"](around:{radius_m},{lat},{lng});\n'
        f'  node["amenity"="parking"](around:{radius_m},{lat},{lng});\n'
        f'  way["amenity"="parking"](around:{radius_m},{lat},{lng});\n'
        f'  way["highway"~"^(primary|secondary|tertiary|unclassified)$"](around:{radius_m},{lat},{lng});\n'  # noqa: E501
        f'  node["building"~"^(yes|house|residential|commercial|industrial|garage|shed)$"](around:{radius_m},{lat},{lng});\n'  # noqa: E501
        f'  way["building"~"^(yes|house|residential|commercial|industrial|garage|shed)$"](around:{radius_m},{lat},{lng});\n'  # noqa: E501
        f");\n"
        f"out geom;"
    )
    data = _overpass_query(query)
    points = []
    for element in data.get("elements", []):
        point = _parse_access_point(element)
        if point is not None:
            points.append(point)
    return points


# How far from the river line each kind of feature is searched for. Dams and
# roads must be on the water; parking and trailheads only need to be a walk away.
_CORRIDOR_STRUCTURE_M = 100
_CORRIDOR_ACCESS_M = 300
_CORRIDOR_ROAD_M = 60
# Vertices per Overpass query when following a river (see fetch_stream_corridor).
_CORRIDOR_CHUNK_VERTICES = 24
_CROSSING_ROADS = "trunk|primary|secondary|tertiary|unclassified|residential"


def fetch_stream_corridor(
    line: list[tuple[float, float]], jurisdiction: str | None = None
) -> list[CorridorFeature]:
    """Dams, access spots and crossing roads beside a river line. Cached 30 days.

    `line` is (lon, lat) vertices along the river, as for a stretch's
    geometry. One query per river rather than a radius around a point: the
    radius fetch above covers the home area only, and a stretch two hours
    away has no rows in access_points to read. A long river is queried in
    overlapping pieces, each cached on its own, so no single query is large
    and an outage part-way through costs the pieces not yet fetched, not all
    of them.

    Raises on an Overpass outage, after the usual retries. An empty list
    means Overpass answered and found nothing — the two are different facts.
    """
    if len(line) < 2:
        return []
    found: dict[str, CorridorFeature] = {}
    step = _CORRIDOR_CHUNK_VERTICES - 1  # pieces share an end vertex: no gap between them
    for start in range(0, len(line) - 1, step):
        piece = line[start : start + _CORRIDOR_CHUNK_VERTICES]
        data = _overpass_query(_corridor_query(piece))
        for element in data.get("elements", []):
            feature = _parse_corridor_element(element, jurisdiction)
            if feature is not None:
                found.setdefault(feature.osm_id, feature)
    return list(found.values())


def fetch_shore_structures(
    bboxes: list[tuple[float, float, float, float]],
    jurisdiction: str | None = None,
) -> tuple[list[ShoreStructure], int]:
    """Piers, breakwaters and harbours at a lake shore. Cached 30 days.

    `bboxes` are (min_lon, min_lat, max_lon, max_lat) cells the shoreline
    passes through — the same grid the lake's inlets are fetched on, so a
    structure beside a lake two hours away is still found. One query per cell,
    each cached on its own, so a re-run over an overlapping lake is offline.

    A cell Overpass cannot answer (after the usual retries) is split in four and
    each quarter tried; a quarter that still fails is logged at WARNING and
    skipped, so one heavy cell does not lose the lake's other structures and a
    re-run fetches only what is missing (everything answered is cached). Returns
    the structures and the number of cells skipped, so a partly answered lake is
    reported rather than silently thin. Raises only when every cell failed. An
    empty list with no skipped cells means Overpass answered and found nothing.
    """
    found: dict[str, ShoreStructure] = {}
    failed: list[tuple[float, float, float, float]] = []
    answered = 0

    def fetch(cell: tuple[float, float, float, float], depth: int = 0) -> None:
        nonlocal answered
        min_lon, min_lat, max_lon, max_lat = cell
        try:
            data = _overpass_query(_shore_structure_query(min_lat, min_lon, max_lat, max_lon))
        except Exception as exc:
            if depth == 0:
                mid_lon, mid_lat = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
                for quarter in (
                    (min_lon, min_lat, mid_lon, mid_lat),
                    (mid_lon, min_lat, max_lon, mid_lat),
                    (min_lon, mid_lat, mid_lon, max_lat),
                    (mid_lon, mid_lat, max_lon, max_lat),
                ):
                    fetch(quarter, depth + 1)
                return
            logger.warning("shore structures: cell %s skipped: %s", cell, exc)
            failed.append(cell)
            return
        answered += 1
        for element in data.get("elements", []):
            structure = _parse_shore_structure(element, jurisdiction)
            if structure is not None:
                found.setdefault(structure.osm_id, structure)

    for cell in bboxes:
        fetch(cell)
    if failed and not answered:
        raise RuntimeError(f"Overpass answered none of {len(failed)} shore-structure cells")
    return list(found.values()), len(failed)


def _shore_structure_query(south: float, west: float, north: float, east: float) -> str:
    bbox = f"({south:.5f},{west:.5f},{north:.5f},{east:.5f})"
    return (
        "[out:json][timeout:60];\n"
        "(\n"
        f'  nw["man_made"="pier"]{bbox};\n'
        f'  nw["man_made"="breakwater"]{bbox};\n'
        f'  nw["harbour"="yes"]{bbox};\n'
        ");\n"
        "out geom tags;"
    )


def _corridor_query(line: list[tuple[float, float]]) -> str:
    poly = ",".join(f"{lat:.5f},{lon:.5f}" for lon, lat in line)

    def near(metres: int) -> str:
        return f"(around:{metres},{poly})"

    structure, access, road = (
        near(_CORRIDOR_STRUCTURE_M),
        near(_CORRIDOR_ACCESS_M),
        near(_CORRIDOR_ROAD_M),
    )
    return (
        "[out:json][timeout:60];\n"
        "(\n"
        f'  nwr["waterway"~"^(dam|weir|lock_gate)$"]{structure};\n'
        f'  nwr["amenity"~"^(parking|boat_ramp)$"]{access};\n'
        f'  nwr["leisure"~"^(slipway|fishing)$"]{access};\n'
        f'  nwr["highway"~"^(trailhead|layby)$"]{access};\n'
        ")->.points;\n"
        f'way["highway"~"^({_CROSSING_ROADS})$"]{road}->.roads;\n'
        ".points out center tags;\n"
        ".roads out geom tags;"
    )


def _overpass_query(query_str: str) -> dict:
    """POST to Overpass API with 30-day file cache. Sleeps 0.5s before live requests."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(query_str.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    time.sleep(0.5)
    last_exc: Exception = RuntimeError("No Overpass endpoints available")
    for attempt in range(len(_RETRY_DELAYS) + 1):
        if attempt > 0:
            time.sleep(_RETRY_DELAYS[attempt - 1])
        for url in _OVERPASS_URLS:
            try:
                response = httpx.post(
                    url,
                    data={"data": query_str},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=90,
                )
                response.raise_for_status()
                data = response.json()
                # Overpass answers a failed or timed-out query with HTTP 200 and
                # either an HTML error page or a partial result with a remark.
                # Neither is data, and neither may be cached for 30 days.
                if "runtime error" in str(data.get("remark", "")):
                    raise RuntimeError(f"Overpass runtime error: {data['remark']}")
                cache_file.write_text(json.dumps(data))
                return data
            except (
                httpx.HTTPStatusError,
                httpx.TimeoutException,
                httpx.ConnectError,
                ValueError,
                RuntimeError,
            ) as e:
                last_exc = e
    raise last_exc


def _parse_water_feature(element: dict) -> WaterFeature | None:
    etype = element.get("type")
    eid = element.get("id")
    tags = element.get("tags", {})

    feature_type = _water_feature_type(tags)
    if feature_type is None:
        return None

    centroid = _centroid(element)
    if centroid is None:
        return None
    c_lat, c_lng = centroid

    geometry = element.get("geometry", [])
    area_m2 = _polygon_area_m2(geometry) if etype == "way" else None

    return WaterFeature(
        osm_id=f"{etype}/{eid}",
        feature_type=feature_type,
        name=tags.get("name") or None,
        lat=c_lat,
        lng=c_lng,
        jurisdiction=jurisdiction_for_coords(c_lat, c_lng),
        area_m2=area_m2,
        tags=tags,
    )


def _parse_access_point(element: dict) -> AccessPoint | None:
    etype = element.get("type")
    eid = element.get("id")
    tags = element.get("tags", {})

    access_type = _access_type_from_tags(tags)
    if access_type is None:
        return None

    centroid = _centroid(element)
    if centroid is None:
        return None
    c_lat, c_lng = centroid

    return AccessPoint(
        osm_id=f"{etype}/{eid}",
        access_type=access_type,
        name=tags.get("name") or None,
        lat=c_lat,
        lng=c_lng,
        jurisdiction=jurisdiction_for_coords(c_lat, c_lng),
        tags=tags,
    )


def _parse_corridor_element(element: dict, jurisdiction: str | None) -> CorridorFeature | None:
    tags = element.get("tags", {})
    kind = _corridor_kind(tags)
    if kind is None:
        return None

    line = None
    if kind == "road":
        geometry = element.get("geometry", [])
        line = [(g["lon"], g["lat"]) for g in geometry if "lat" in g and "lon" in g]
        if len(line) < 2:
            return None
        lat = sum(y for _, y in line) / len(line)
        lng = sum(x for x, _ in line) / len(line)
    else:
        centre = element.get("center") or element
        if "lat" not in centre or "lon" not in centre:
            return None
        lat, lng = float(centre["lat"]), float(centre["lon"])

    return CorridorFeature(
        osm_id=f"{element.get('type')}/{element.get('id')}",
        kind=kind,
        name=tags.get("name") or None,
        lat=lat,
        lng=lng,
        jurisdiction=jurisdiction or jurisdiction_for_coords(lat, lng),
        tags=tags,
        line=line,
    )


def _corridor_kind(tags: dict) -> str | None:
    waterway = tags.get("waterway")
    if waterway == "dam":
        return "dam"
    if waterway == "weir":
        return "weir"
    if waterway == "lock_gate":
        return "lock"
    if tags.get("amenity") == "boat_ramp" or tags.get("leisure") == "slipway":
        return "boat_launch"
    if tags.get("leisure") == "fishing":
        return "fishing_spot"
    if tags.get("highway") == "trailhead":
        return "trail_head"
    if tags.get("amenity") == "parking" or tags.get("highway") == "layby":
        return "parking"
    if tags.get("highway"):
        return "road"
    return None


def _parse_shore_structure(element: dict, jurisdiction: str | None) -> ShoreStructure | None:
    tags = element.get("tags", {})
    kind = _shore_structure_kind(tags)
    if kind is None:
        return None
    geometry = _element_geojson(element)
    if geometry is None:
        return None
    centroid = _centroid(element)
    if centroid is None:
        return None
    lat, lng = centroid

    return ShoreStructure(
        osm_id=f"{element.get('type')}/{element.get('id')}",
        kind=kind,
        name=tags.get("name") or None,
        geometry=geometry,
        lat=lat,
        lng=lng,
        jurisdiction=jurisdiction or jurisdiction_for_coords(lat, lng),
        tags=tags,
    )


def _shore_structure_kind(tags: dict) -> str | None:
    if tags.get("man_made") == "pier":
        return "pier"
    if tags.get("man_made") == "breakwater":
        return "breakwater"
    if tags.get("harbour") == "yes":
        return "harbour"
    return None


def _element_geojson(element: dict) -> dict | None:
    """A node/way element as GeoJSON: Point, LineString or closed-way Polygon.

    A way whose first and last vertex coincide is a ring and becomes a Polygon;
    anything else is a LineString. None when there is no usable geometry.
    """
    etype = element.get("type")
    if etype == "node":
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is None or lon is None:
            return None
        return {"type": "Point", "coordinates": [float(lon), float(lat)]}
    geometry = element.get("geometry", [])
    coords = [[float(g["lon"]), float(g["lat"])] for g in geometry if "lat" in g and "lon" in g]
    if len(coords) < 2:
        return None
    if len(coords) >= 4 and coords[0] == coords[-1]:
        return {"type": "Polygon", "coordinates": [coords]}
    return {"type": "LineString", "coordinates": coords}


def _water_feature_type(tags: dict) -> str | None:
    waterway = tags.get("waterway", "")
    natural = tags.get("natural", "")
    water = tags.get("water", "")

    if waterway == "river":
        return "river"
    if waterway == "stream":
        return "stream"
    if waterway == "canal":
        return "canal"
    if waterway == "ditch":
        return "ditch"
    if waterway == "drain":
        return "drain"
    if natural == "wetland":
        return "wetland"
    if natural == "bay":
        return "bay"
    if natural == "water":
        if water == "pond":
            return "pond"
        if water == "reservoir":
            return "reservoir"
        return "lake"
    return None


def _access_type_from_tags(tags: dict) -> str | None:
    if tags.get("amenity") == "boat_ramp":
        return "boat_launch"
    if tags.get("leisure") == "fishing":
        return "fishing_spot"
    if tags.get("highway") == "trailhead":
        return "trail_head"
    if tags.get("leisure") == "nature_reserve":
        return "conservation_area"
    if tags.get("boundary") == "protected_area":
        return "public_land"
    if tags.get("leisure") == "park":
        return "park"
    if tags.get("highway") == "layby":
        return "parking"
    if tags.get("amenity") == "parking":
        return "parking"
    if tags.get("highway") in ("primary", "secondary", "tertiary", "unclassified"):
        return "road"
    if tags.get("building"):
        return "building"
    return None


def _centroid(element: dict) -> tuple[float, float] | None:
    etype = element.get("type")
    if etype == "node":
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
        return None
    if etype in ("way", "relation"):
        geometry = element.get("geometry", [])
        if not geometry:
            return None
        lats = [g["lat"] for g in geometry if "lat" in g]
        lons = [g["lon"] for g in geometry if "lon" in g]
        if not lats or not lons:
            return None
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return None


def _polygon_area_m2(geometry: list[dict]) -> float | None:
    """Shoelace formula for a closed-way polygon. Returns approximate area in m²."""
    if len(geometry) < 4:
        return None
    first, last = geometry[0], geometry[-1]
    if first["lat"] != last["lat"] or first["lon"] != last["lon"]:
        return None  # open line, not a polygon

    avg_lat = sum(g["lat"] for g in geometry) / len(geometry)
    lat_m = 111_132.0
    lng_m = 111_132.0 * math.cos(math.radians(avg_lat))

    pts = [(g["lon"] * lng_m, g["lat"] * lat_m) for g in geometry]
    n = len(pts)
    area = 0.0
    for i in range(n - 1):
        area += pts[i][0] * pts[i + 1][1]
        area -= pts[i + 1][0] * pts[i][1]
    return abs(area) / 2.0
