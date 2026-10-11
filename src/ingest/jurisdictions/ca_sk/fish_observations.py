"""Saskatchewan fish observation records — SK Fish Inventory / Fish Atlas.

Source: SaskGIS Fisheries service (ArcGIS REST)
  Species Info (waterbody points)         : Fisheries/FeatureServer/1
  FishAtlasHabiSask (species-at-waterbody) : Fisheries/FeatureServer/3
  https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/

The Saskatchewan Fish Inventory stores records as a species-to-waterbody
table (`FishAtlasHabiSask`) with no geometry. Each row carries an `NRCANCODE`
(a 5-character waterbody code) that joins to the Species Info point layer's
`LOCATIONID`. To get location-bound observation rows we therefore:

  1. Query the Species Info point layer within a bbox → waterbody coords.
  2. Query the species table for those `NRCANCODE`s (chunked IN list).
  3. Emit one Observation per (waterbody, species) row.

Observations are stored in the shared `observations` table with
source='SKFishAtlas' and jurisdiction='CA-SK'. The table's OBJECTID (a
provincial integer, ~6 digits) is used as observation_id — the same
native-integer convention as FISS; see JURISDICTION_TEMPLATE.md for the
cross-source collision note.

The source has no observation date, so observed_on uses the shared
"unknown date" sentinel (1900-01-01). Waterbody codes beginning "HXX" are the
province's "unnamed/unknown location" placeholders; those points carry no
usable coordinates and are dropped (and counted).

Cache TTL: 30 days.
"""

import hashlib
import json
import logging
import math
import time
from datetime import date
from pathlib import Path

import httpx

from src.models.observation import Observation

_POINTS_URL = "https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/1/query"
_SPECIES_URL = "https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/3/query"
_PAGE_SIZE = 1000
_CHUNK_SIZE = 200
_CACHE_DIR = Path("data/cache/sk_fish_atlas")
_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# The source publishes no catch/observation date.
_UNKNOWN_DATE = date(1900, 1, 1)

logger = logging.getLogger(__name__)


def fetch_observations(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[Observation]:
    """Fetch SK Fish Atlas observations within radius_km of lat/lng. Cached 30 days."""
    locations = _fetch_waterbody_points(lat, lng, radius_km)
    if not locations:
        logger.info("SK Fish Atlas: no waterbody points in %.0fkm radius", radius_km)
        return []

    codes = list(locations.keys())
    logger.info("SK Fish Atlas: %d waterbodies in radius; querying species rows", len(codes))

    observations: list[Observation] = []
    seen: set[int] = set()
    n_orphan = 0
    for chunk in _chunks(codes, _CHUNK_SIZE):
        for row in _fetch_species_rows(chunk):
            fid = row.get("OBJECTID")
            if fid is None or fid in seen:
                continue
            species = (row.get("S_PRIMARY_COMMON_NAME") or "").strip()
            loc = locations.get((row.get("NRCANCODE") or "").strip())
            if not species:
                continue
            if loc is None:
                n_orphan += 1
                continue
            seen.add(fid)
            observations.append(
                Observation(
                    observation_id=int(fid),
                    species=species,
                    common_name=species,
                    taxon_id=None,
                    lat=loc["lat"],
                    lng=loc["lng"],
                    observed_on=_UNKNOWN_DATE,
                    quality_grade="survey_data",
                    photo_url=None,
                    observer=None,
                    place_guess=(row.get("WATERBODY") or "").strip() or loc["name"],
                    jurisdiction="CA-SK",
                    geoprivacy="open",
                    is_obscured=False,
                    obscuration_radius_km=None,
                    source="SKFishAtlas",
                )
            )

    if n_orphan:
        logger.warning(
            "SK Fish Atlas: %d species rows referenced a waterbody with no coordinates",
            n_orphan,
        )
    logger.info("SK Fish Atlas fetch complete: %d observations", len(observations))
    return observations


# ── step 1: waterbody points ────────────────────────────────────────────────────


def _fetch_waterbody_points(lat: float, lng: float, radius_km: float) -> dict[str, dict]:
    """Return {LOCATIONID: {lat, lng, name}} for waterbodies in the bbox."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    params = {
        "where": "1=1",
        "geometry": f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "LOCATIONID,NAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    locations: dict[str, dict] = {}
    for feat in _paginate(_POINTS_URL, params):
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        code = (props.get("LOCATIONID") or "").strip()
        if not code or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        # First valid point wins (the layer repeats some codes with blank geometry).
        locations.setdefault(
            code,
            {
                "lat": float(coords[1]),
                "lng": float(coords[0]),
                "name": (props.get("NAME") or "").strip() or None,
            },
        )
    return locations


# ── step 2: species rows ────────────────────────────────────────────────────────


def _fetch_species_rows(codes: list[str]) -> list[dict]:
    quoted = ",".join("'" + c.replace("'", "''") + "'" for c in codes)
    params = {
        "where": f"NRCANCODE IN ({quoted})",
        "outFields": "OBJECTID,S_PRIMARY_COMMON_NAME,WATERBODY,NRCANCODE",
        "returnGeometry": "false",
        "f": "json",
    }
    return [feat.get("attributes") or {} for feat in _paginate(_SPECIES_URL, params)]


def _paginate(url: str, base_params: dict) -> list[dict]:
    """Page through an ArcGIS query. Raises on any error so a partial read never looks complete."""
    features: list[dict] = []
    offset = 0
    while True:
        params = {**base_params, "resultOffset": offset, "resultRecordCount": _PAGE_SIZE}
        data = _cached_get(url, params)
        page = data.get("features", [])
        features.extend(page)
        if len(page) < _PAGE_SIZE:
            return features
        offset += _PAGE_SIZE


# ── HTTP + file cache ──────────────────────────────────────────────────────────


def _cached_get(url: str, params: dict) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_key = url + str(sorted(params.items()))
    key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    response = httpx.get(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"SK Fish Atlas: ArcGIS error: {data['error']}")
    cache_file.write_text(json.dumps(data))
    return data


# ── helpers ────────────────────────────────────────────────────────────────────


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
