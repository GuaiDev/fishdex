"""eBird piscivore observation fetcher (global adapter).

Queries the eBird API v2 for recent observations of five fish-eating bird
species near a geographic point.  Piscivore activity is an independent
biological signal for fish presence: osprey and mergansers only hunt where
fish are catchable; herons and kingfishers indicate accessible shallow-water
fish habitat.

API key required — set EBIRD_API_KEY in .env.  Returns [] with a warning log
when the key is absent (graceful skip during ingest).

Responses are cached to data/cache/ebird/ with a 24-hour TTL, keyed by
species code + rounded lat/lng + date so the same location+species+day
always hits cache.
"""

import hashlib
import json
import logging
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.bird_observation import BirdObservation
from src.services.ingest_runs import record_counts, record_error

load_dotenv()

_BASE_URL = "https://api.ebird.org/v2/data/obs/geo/recent"
_CACHE_DIR = Path("data/cache/ebird")
_CACHE_TTL_SECONDS = 86400  # 24 hours
_INTER_SPECIES_DELAY = 0.5  # seconds between API calls

logger = logging.getLogger(__name__)

# The five piscivore species we track.  All are strong, independent
# indicators of fish-bearing water — they don't hunt where fish aren't present.
PISCIVORE_CODES = ["grbher3", "osprey1", "belkin1", "commer1", "doccor"]

_SIGNIFICANCE: dict[str, str] = {
    "grbher3": ("Active hunting indicates shallow fish-bearing water within foraging range"),
    "osprey1": ("Confirmed fish presence — osprey only hunt where fish are abundant and catchable"),
    "belkin1": ("Strong indicator of small fish in clear, accessible water"),
    "commer1": ("Diving pursuit predator — confirms fish present at depth in rivers and lakes"),
    "doccor": ("Colonial fish predator — high densities indicate productive fish habitat"),
}


def fetch_piscivore_observations(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int = 30,
) -> list[BirdObservation]:
    """Fetch piscivore observations from eBird for all 5 target species.

    Returns [] immediately if EBIRD_API_KEY is not set.
    Clamps dist to 50 and back to 30 per eBird API limits.
    """
    api_key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not api_key:
        record_counts(skipped=1)
        logger.warning(
            "EBIRD_API_KEY not set — skipping eBird piscivore fetch. "
            "Set EBIRD_API_KEY in .env to enable."
        )
        return []

    dist = min(int(radius_km), 50)
    back = min(int(days_back), 30)
    today = date.today().isoformat()

    all_obs: list[BirdObservation] = []
    for i, species_code in enumerate(PISCIVORE_CODES):
        if i > 0:
            time.sleep(_INTER_SPECIES_DELAY)
        record_counts(fetched=1)
        try:
            raw = _cached_get(api_key, species_code, lat, lng, dist, back, today)
            obs = _parse_response(raw, species_code)
            record_counts(empty=int(not obs), skipped=len(raw) - len(obs))
            all_obs.extend(obs)
            logger.info("  eBird %s: %d observations", species_code, len(obs))
        except httpx.HTTPError as exc:
            record_counts(skipped=1)
            record_error(exc)
            logger.warning("eBird fetch failed for %s: %s", species_code, exc)

    return all_obs


def _cache_key(species_code: str, lat: float, lng: float, dist: int, back: int, today: str) -> str:
    payload = f"{species_code}_{lat:.2f}_{lng:.2f}_{dist}_{back}_{today}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _cached_get(
    api_key: str,
    species_code: str,
    lat: float,
    lng: float,
    dist: int,
    back: int,
    today: str,
) -> list[dict]:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(species_code, lat, lng, dist, back, today)
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            logger.debug("eBird cache hit: %s", cache_file.name)
            return json.loads(cache_file.read_text())

    url = f"{_BASE_URL}/{species_code}"
    params = {"lat": lat, "lng": lng, "dist": dist, "back": back, "maxResults": 1000}
    headers = {"X-eBirdApiToken": api_key}

    response = httpx.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


def _parse_response(raw: list[dict], expected_code: str) -> list[BirdObservation]:
    obs: list[BirdObservation] = []
    significance = _SIGNIFICANCE.get(expected_code, "Piscivore — indicates fish-bearing water")

    for item in raw:
        species_code = item.get("speciesCode", expected_code)
        sub_id = item.get("subId", "")
        if not sub_id:
            continue

        lat = item.get("lat")
        lng = item.get("lng")
        if lat is None or lng is None:
            continue

        obs_dt_str = item.get("obsDt", "")
        try:
            # eBird returns "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"
            observed_on = date.fromisoformat(obs_dt_str[:10])
        except (ValueError, IndexError):
            continue

        how_many_raw = item.get("howMany")
        how_many: int | None = None
        if how_many_raw is not None:
            try:
                how_many = int(how_many_raw)
            except (ValueError, TypeError):
                pass

        obs.append(
            BirdObservation(
                obs_id=f"{sub_id}_{species_code}",
                species_code=species_code,
                common_name=item.get("comName", ""),
                scientific_name=item.get("sciName") or None,
                lat=float(lat),
                lng=float(lng),
                observed_on=observed_on,
                how_many=how_many,
                location_name=item.get("locName") or None,
                jurisdiction=jurisdiction_for_coords(float(lat), float(lng)),
                piscivore_significance=significance,
                fetched_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    return obs
