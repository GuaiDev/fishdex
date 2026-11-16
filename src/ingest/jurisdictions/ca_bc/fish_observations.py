"""BC FISS Known Fish Observations ingestion via DataBC WFS.

Source: DataBC Open Data — WFS 2.0.0
  Layer: WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP
  Endpoint: https://openmaps.gov.bc.ca/geo/pub/WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP/ows

FISS (Fish Inventory Summary System) contains known fish presence records
from electrofishing surveys, snorkel counts, capture-mark-recapture studies,
and incidental observations going back to the 1950s.

Observations are stored in the shared `observations` table with source='FISS'
and jurisdiction='CA-BC'. The FISH_OBSERVATION_POINT_ID (a 6-to-7-digit
provincial integer) is used as observation_id. iNaturalist IDs are in the
hundreds of millions; collision with FISS IDs is extremely unlikely in practice.

Cache TTL: 7 days (observation records can be updated or corrected by FLNR).
"""

import hashlib
import json
import logging
import math
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from src.models.observation import Observation
from src.services.ingest_runs import record_counts, record_error

_WFS_URL = (
    "https://openmaps.gov.bc.ca/geo/pub/"
    "WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP/ows"
)
_TYPE_NAME = "pub:WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP"
_PAGE_SIZE = 1000
_CACHE_DIR = Path("data/cache/fiss")
_CACHE_TTL_SECONDS = 7 * 86400  # 7 days
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# FISS records without a date use this sentinel so the model validates
_UNKNOWN_DATE = date(1900, 1, 1)

# FISS ACTIVITY_CODE values that indicate stocking events (not wild observations)
_STOCKING_ACTIVITY_CODES: frozenset[str] = frozenset({
    "STO", "STK", "STOCK", "PLANT", "STOCKING", "HATCHERY",
})

logger = logging.getLogger(__name__)


def fetch_observations(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[Observation]:
    """Fetch FISS fish observation points within radius_km of lat/lng. Cached 7 days."""
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    # ",CRS:84" tells the server to interpret bbox coords as lon/lat; without it
    # the server treats them as native BC Albers metres and returns 0 results.
    bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f},CRS:84"

    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": _TYPE_NAME,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": bbox_str,
        # propertyName omitted — FISS layer rejects unknown field names with 400.
        # Fetch all properties and filter client-side.
    }

    features = _wfs_paginate(_WFS_URL, base_params)
    logger.info("FISS: received %d raw features for %.0fkm radius", len(features), radius_km)

    observations: list[Observation] = []
    seen: set[int] = set()
    for feat in features:
        obs = _parse_observation(feat)
        if obs is not None and obs.observation_id not in seen:
            seen.add(obs.observation_id)
            observations.append(obs)

    logger.info("FISS fetch complete: %d observations", len(observations))
    return observations


# ── WFS pagination ─────────────────────────────────────────────────────────────


def _wfs_paginate(url: str, base_params: dict) -> list[dict]:
    features: list[dict] = []
    start = 0
    while True:
        params = {**base_params, "startIndex": start, "count": _PAGE_SIZE}
        record_counts(fetched=1)
        try:
            data = _cached_get(url, params)
        except Exception as exc:
            record_counts(skipped=1)
            record_error(exc)
            logger.error("FISS WFS request failed at startIndex=%d: %s", start, exc)
            break
        page = data.get("features", [])
        record_counts(empty=int(not page))
        features.extend(page)
        logger.debug("FISS WFS startIndex=%d: %d features on page", start, len(page))
        if len(page) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return features


# ── parser ─────────────────────────────────────────────────────────────────────


def _parse_observation(feat: dict) -> Observation | None:
    props = feat.get("properties") or {}
    geojson_geom = feat.get("geometry")

    fid = props.get("FISH_OBSERVATION_POINT_ID")
    if fid is None:
        return None

    if geojson_geom and geojson_geom.get("type") == "Point":
        coords = geojson_geom.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat_val = coords[0], coords[1]
        else:
            return None
    else:
        return None

    species_name = (props.get("SPECIES_NAME") or "").strip()
    if not species_name:
        species_name = props.get("SPECIES_CODE") or "Unknown"

    observed_on = _parse_date(props.get("OBSERVATION_DATE"))

    # GAZETTED_NAME is the official waterbody name; fall back to WATERBODY_IDENTIFIER
    place = (props.get("GAZETTED_NAME") or "").strip() or None
    if not place:
        place = (props.get("WATERBODY_IDENTIFIER") or "").strip() or None

    try:
        return Observation(
            observation_id=int(fid),
            species=species_name,
            common_name=species_name,
            taxon_id=None,
            lat=float(lat_val),
            lng=float(lon),
            observed_on=observed_on,
            quality_grade="survey_data",
            photo_url=None,
            observer=None,
            place_guess=place,
            jurisdiction="CA-BC",
            geoprivacy="open",
            is_obscured=False,
            obscuration_radius_km=None,
            source="FISS",
        )
    except (ValueError, TypeError) as exc:
        logger.warning("FISS: skipping observation ID=%s: %s", fid, exc)
        return None


def fetch_stocking_from_fiss(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[dict]:
    """Extract stocking records from FISS (same WFS call, hits cache on second call).

    Filters features where ACTIVITY_CODE matches known stocking codes.
    Returns list of row dicts for stocking_records table.
    Note: FISS stocking records lack quantity data; quantity is set to None.
    """
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f},CRS:84"
    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": _TYPE_NAME,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": bbox_str,
    }
    features = _wfs_paginate(_WFS_URL, base_params)

    stocking_rows: list[dict] = []
    seen: set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        activity_code = (props.get("ACTIVITY_CODE") or "").strip().upper()
        if activity_code not in _STOCKING_ACTIVITY_CODES:
            continue

        geojson_geom = feat.get("geometry") or {}
        if geojson_geom.get("type") != "Point":
            continue
        coords = geojson_geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, feat_lat = float(coords[0]), float(coords[1])

        fid = props.get("FISH_OBSERVATION_POINT_ID")
        if fid is None or str(fid) in seen:
            continue
        seen.add(str(fid))

        species = (props.get("SPECIES_NAME") or props.get("SPECIES_CODE") or "Unknown").strip()
        raw_date = props.get("OBSERVATION_DATE")
        obs_date = _parse_date(raw_date)
        year = obs_date.year if obs_date.year > 1900 else None
        place = (props.get("GAZETTED_NAME") or props.get("WATERBODY_IDENTIFIER") or "").strip()

        stocking_rows.append({
            "record_id": f"FISS_STO_{fid}",
            "waterbody_name": place or None,
            "waterbody_code": (props.get("WATERBODY_IDENTIFIER") or "").strip() or None,
            "municipality": None,
            "county": None,
            "lat": feat_lat,
            "lng": lon,
            "jurisdiction": "CA-BC",
            "species": species,
            "species_code": activity_code,
            "year": year,
            "month": None,
            "quantity": None,
            "life_stage": None,
            "stocking_purpose": "hatchery",
            "stocked_at": obs_date.isoformat() if obs_date.year > 1900 else None,
        })

    logger.info("FISS stocking: %d stocking records extracted from FISS", len(stocking_rows))
    return stocking_rows


def _parse_date(raw: str | None) -> date:
    """Parse FISS observation date; returns _UNKNOWN_DATE for missing/unparseable values."""
    if not raw:
        return _UNKNOWN_DATE
    # WFS GeoJSON dates arrive as "1999-04-19Z" or "2010-07-15T00:00:00Z"; strip the Z.
    raw = raw.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    logger.debug("FISS: could not parse date %r, using sentinel", raw)
    return _UNKNOWN_DATE


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
    if response.status_code >= 400:
        logger.error(
            "FISS WFS HTTP %d — response body: %s",
            response.status_code,
            response.text[:500],
        )
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


# ── geometry helpers ───────────────────────────────────────────────────────────


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_deg = radius_km / 111.0
    lon_deg = radius_km / (111.320 * math.cos(math.radians(lat)))
    return (lon - lon_deg, lat - lat_deg, lon + lon_deg, lat + lat_deg)
