"""Ontario MRD 128 surficial geology tile ingestion (CA-ON).

Downloads the tile index KML from GeologyOntario then fetches only the
0.5°×0.5° KMZ tiles that intersect the user's home bounding box.  Each tile
KMZ is cached locally for 365 days — the dataset is from 2010 and does not
change.

Parsing uses only stdlib: zipfile + xml.etree.ElementTree.  No GDAL, fiona,
or geopandas required.

The centroid for each polygon unit is taken from the <Point> element that the
source data places inside each <MultiGeometry>.  The polygon rings are used
only to compute a bounding box for the unit.
"""

import logging
import math
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx

from src.models.geology_unit import GeologyUnit

_TILE_INDEX_URL = (
    "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/mrd128/polygons/doc.kml"
)
_TILE_BASE_URL = "https://www.geologyontario.mndm.gov.on.ca/mines/data/google/mrd128/polygons/"
_INDEX_PATH = Path("data/raw/mrd128_index.kml")
_TILE_DIR = Path("data/raw/mrd128_tiles/")
_TTL = 365 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
_KML_NS = "http://earth.google.com/kml/2.2"

logger = logging.getLogger(__name__)

# Substrate classification table — maps unit code → substrate class.
# Unit 21 (man-made) is omitted; _classify_substrate returns None for it.
_SUBSTRATE_MAP: dict[str, str] = {
    "1": "bedrock",
    "2": "bedrock",
    "3": "bedrock",
    "4": "bedrock",
    "5a": "mixed",
    "5b": "mixed",
    "5c": "mixed",
    "5d": "mixed",
    "5e": "mixed",
    "6": "coarse",
    "6a": "coarse",
    "7": "coarse",
    "8a": "fine",
    "8b": "fine",
    "9": "coarse",
    "9a": "coarse",
    "9b": "coarse",
    "9c": "coarse",
    "20": "organic",
}


def _classify_substrate(unit_code: str) -> str | None:
    """Return substrate class for a unit code, or None to skip (unit 21 = man-made)."""
    if unit_code == "21":
        return None
    return _SUBSTRATE_MAP.get(unit_code, "mixed")


def _tag(name: str) -> str:
    return f"{{{_KML_NS}}}{name}"


def _find(el: ET.Element, *path: str) -> ET.Element | None:
    """Walk a sequence of tag names as direct children from el."""
    cur: ET.Element | None = el
    for name in path:
        if cur is None:
            return None
        cur = cur.find(_tag(name))
    return cur


def _findall_desc(el: ET.Element, name: str) -> list[ET.Element]:
    """Find all descendants with the given tag name."""
    return el.findall(f".//{_tag(name)}")


def _text(el: ET.Element | None) -> str:
    if el is None or not el.text:
        return ""
    return el.text.strip()


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def _parse_coords(coords_text: str) -> list[tuple[float, float]]:
    """Parse KML 'lon,lat,alt ...' text → list of (lon, lat) pairs."""
    pts: list[tuple[float, float]] = []
    for token in coords_text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                pass
    return pts


def _download_if_stale(url: str, path: Path, ttl: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if time.time() - path.stat().st_mtime < ttl:
            return path
    logger.info("Downloading %s …", url)
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        timeout=120,
    ) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=8192):
                f.write(chunk)
    logger.info("Saved %s (%.1f KB)", path.name, path.stat().st_size / 1024)
    return path


def download_tile_index() -> list[tuple[str, tuple[float, float, float, float], str]]:
    """Fetch the tile index KML.

    Returns [(tile_id, (west, south, east, north), href_relative_path)].
    The href is relative to _TILE_BASE_URL (e.g. "files/-83.5_42_-83_42.5.kmz").
    """
    index_path = _download_if_stale(_TILE_INDEX_URL, _INDEX_PATH, _TTL)
    tree = ET.parse(index_path)
    root = tree.getroot()

    tiles: list[tuple[str, tuple[float, float, float, float], str]] = []
    for nl in _findall_desc(root, "NetworkLink"):
        name_el = _find(nl, "name")
        tile_id = _text(name_el)

        # href is under Link/href
        link_el = _find(nl, "Link")
        href_el = _find(link_el, "href") if link_el is not None else None
        href = _text(href_el)
        if not href:
            continue

        # bbox is under Region/LatLonAltBox
        region_el = _find(nl, "Region")
        llb = _find(region_el, "LatLonAltBox") if region_el is not None else None
        if llb is None:
            continue
        try:
            west = float(_text(_find(llb, "west")))
            south = float(_text(_find(llb, "south")))
            east = float(_text(_find(llb, "east")))
            north = float(_text(_find(llb, "north")))
        except ValueError:
            continue

        if not tile_id:
            tile_id = href.split("/")[-1].replace(".kmz", "")

        tiles.append((tile_id, (west, south, east, north), href))

    logger.info("Tile index: %d tiles", len(tiles))
    return tiles


def download_tile(tile_id: str, href: str) -> Path:
    """Download a tile KMZ. Cached for 365 days. Returns local path."""
    tile_path = _TILE_DIR / f"{tile_id}.kmz"
    url = _TILE_BASE_URL + href
    return _download_if_stale(url, tile_path, _TTL)


def _parse_tile(kmz_path: Path, tile_id: str) -> list[GeologyUnit]:
    """Unzip and parse a tile KMZ → list[GeologyUnit].

    Uses the <Point> inside each <MultiGeometry> as the centroid.
    Polygon ring coordinates are used only for bbox computation.
    Placemarks with unit code 21 (man-made) are skipped.
    """
    try:
        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_names = [n for n in zf.namelist() if n.endswith(".kml")]
            if not kml_names:
                logger.warning("No .kml inside %s", kmz_path.name)
                return []
            kml_data = zf.read(kml_names[0])
    except (zipfile.BadZipFile, KeyError) as exc:
        logger.warning("Cannot read KMZ %s: %s", kmz_path.name, exc)
        return []

    try:
        root = ET.fromstring(kml_data)
    except ET.ParseError as exc:
        logger.warning("Cannot parse KML in %s: %s", kmz_path.name, exc)
        return []

    units: list[GeologyUnit] = []
    seq = 0

    for pm in _findall_desc(root, "Placemark"):
        name_text = _text(_find(pm, "name"))
        if not name_text:
            continue

        tokens = name_text.split(None, 1)
        unit_code = tokens[0].lower()
        unit_name = tokens[1] if len(tokens) > 1 else name_text

        substrate_class = _classify_substrate(unit_code)
        if substrate_class is None:
            continue  # skip man-made (unit 21)

        desc_text = _text(_find(pm, "description"))
        primary_material = _strip_html(desc_text) or None

        # Centroid: use the <Point> inside <MultiGeometry> when present.
        centroid_lat: float | None = None
        centroid_lng: float | None = None
        pt_el = _find(pm, "MultiGeometry", "Point")
        if pt_el is None:
            pt_el = pm.find(_tag("Point"))
        if pt_el is not None:
            coords_el = _find(pt_el, "coordinates")
            pts = _parse_coords(_text(coords_el))
            if pts:
                centroid_lng, centroid_lat = pts[0]

        # Bbox: collect all polygon ring coordinates.
        all_pts: list[tuple[float, float]] = []
        for coords_el in _findall_desc(pm, "coordinates"):
            raw = _text(coords_el)
            if raw:
                all_pts.extend(_parse_coords(raw))

        if not all_pts:
            continue  # no geometry at all

        lons = [p[0] for p in all_pts]
        lats = [p[1] for p in all_pts]
        minx, maxx = min(lons), max(lons)
        miny, maxy = min(lats), max(lats)

        # Fall back to bbox centroid when no Point element is available.
        if centroid_lat is None or centroid_lng is None:
            centroid_lat = (miny + maxy) / 2
            centroid_lng = (minx + maxx) / 2

        units.append(
            GeologyUnit(
                unit_id=f"{tile_id}_{seq:04d}",
                tile_id=tile_id,
                unit_code=unit_code,
                unit_name=unit_name,
                primary_material=primary_material,
                substrate_class=substrate_class,
                centroid_lat=centroid_lat,
                centroid_lng=centroid_lng,
                bbox_minx=minx,
                bbox_miny=miny,
                bbox_maxx=maxx,
                bbox_maxy=maxy,
            )
        )
        seq += 1

    return units


def load_geology(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[GeologyUnit]:
    """Download and parse all tiles that intersect a radius around (lat, lng).

    Only tiles whose bbox overlaps the query bbox are fetched.  The dataset
    is stable (2010), so tiles are cached locally for a full year.
    """
    deg_lat = radius_km / 111.0
    deg_lng = radius_km / (111.0 * math.cos(math.radians(lat)))
    q_west = lng - deg_lng
    q_east = lng + deg_lng
    q_south = lat - deg_lat
    q_north = lat + deg_lat

    all_tiles = download_tile_index()

    relevant = [
        (tid, bbox, href)
        for tid, bbox, href in all_tiles
        if bbox[2] > q_west and bbox[0] < q_east and bbox[3] > q_south and bbox[1] < q_north
    ]
    logger.info(
        "Home bbox (%.3f,%.3f)–(%.3f,%.3f) intersects %d/%d tiles",
        q_west,
        q_south,
        q_east,
        q_north,
        len(relevant),
        len(all_tiles),
    )

    from src.services.ingest_runs import record_counts

    if not relevant:
        record_counts(fetched=1, empty=1)
    all_units: list[GeologyUnit] = []
    for tid, _bbox, href in relevant:
        kmz_path = download_tile(tid, href)
        units = _parse_tile(kmz_path, tid)
        record_counts(fetched=1, empty=int(not units))
        logger.info("  tile %s: %d units", tid, len(units))
        all_units.extend(units)

    logger.info("Total geology units loaded: %d", len(all_units))
    return all_units
