"""iNaturalist observation fetcher.

Queries fish observations (taxon_id=47178, Actinopterygii) for a geographic area.
All HTTP responses are cached to data/cache/inaturalist/ with a 24-hour TTL.
Requests are rate-limited to 1/sec between pages.
"""

import hashlib
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

from src.jurisdictions.geo import jurisdiction_for_coords
from src.models.observation import Observation
from src.services.ingest_runs import record_counts, record_error

_log = logging.getLogger(__name__)

_API_URL = "https://api.inaturalist.org/v1/observations"
_TAXON_ID = 47178  # Actinopterygii — all bony fish
_CACHE_DIR = Path("data/cache/inaturalist")
_CACHE_TTL_SECONDS = 86400  # 24 hours
_PER_PAGE = 200

# iNaturalist refuses any request whose page × per_page exceeds 10,000 — it
# returns 403, not an empty page. Dense areas over long windows blow through
# this easily: 50km around Oakville over 10 years is well past 10k fish
# records. The loop below stops here instead of walking into the 403.
_MAX_PAGINATED_RESULTS = 10_000


def fetch_observations(
    lat: float,
    lng: float,
    radius_km: float = 50,
    days_back: int | None = 90,
) -> list[Observation]:
    base_params = {
        "taxon_id": _TAXON_ID,
        "lat": lat,
        "lng": lng,
        "radius": radius_km,
        "order_by": "observed_on",
        "order": "desc",
        "per_page": _PER_PAGE,
    }
    if days_back:
        base_params["d1"] = (date.today() - timedelta(days=days_back)).isoformat()

    all_results: list[dict] = []
    page = 1
    total = None

    while True:
        params = {**base_params, "page": page}
        record_counts(fetched=1)
        try:
            raw = _cached_get(params)
        except httpx.HTTPStatusError as exc:
            record_counts(skipped=1)
            record_error(exc)
            # Keep what we already have. Discarding thousands of successfully
            # fetched records because page N+1 was refused helps nobody.
            _log.warning(
                "iNat: page %d refused (%s) — keeping the %d records fetched so far",
                page,
                exc.response.status_code,
                len(all_results),
            )
            break
        results = raw.get("results", [])
        record_counts(empty=int(not results))
        all_results.extend(results)

        if total is None:
            total = raw.get("total_results", 0)

        if len(all_results) >= total or not results:
            break

        if (page + 1) * _PER_PAGE > _MAX_PAGINATED_RESULTS:
            break

        page += 1
        time.sleep(1)

    if total is not None and len(all_results) < total:
        _log.warning(
            "iNat: fetched %d of %d total — API pagination cap reached, %d records "
            "unreachable. Narrow --radius or --days to reach the remainder.",
            len(all_results),
            total,
            total - len(all_results),
        )

    parsed: list[Observation] = []
    n_no_location = n_no_date = 0
    for r in all_results:
        if not _has_location(r):
            n_no_location += 1
            continue
        if _observed_date(r) is None:
            n_no_date += 1
            continue
        parsed.append(_parse_observation(r))

    dropped = n_no_location + n_no_date
    record_counts(skipped=dropped)
    if dropped:
        share = dropped / len(all_results)
        # Material share goes to WARNING; a stray record or two is INFO. Either
        # way the count is emitted — a drop nobody counts is indistinguishable
        # from water nobody surveyed.
        emit = _log.warning if share >= 0.01 else _log.info
        emit(
            "iNat: dropped %d of %d records (%.1f%%) — %d without coordinates, "
            "%d without a parseable observed_on date",
            dropped,
            len(all_results),
            share * 100,
            n_no_location,
            n_no_date,
        )

    return parsed


def _cached_get(params: dict) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text())

    response = httpx.get(_API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    cache_file.write_text(json.dumps(data))
    return data


def _has_location(result: dict) -> bool:
    return bool(result.get("location"))


def _observed_date(result: dict) -> date | None:
    """The record's observation date, or None if it hasn't got a usable one.

    iNaturalist leaves `observed_on` null on undated records and sometimes
    carries only a partial date (year, or year-month) in `observed_on_details`.
    Neither parses as an ISO date. Returning None lets the caller drop the one
    record and count it, which is the whole point: this used to be a bare
    `date.fromisoformat(result["observed_on"])` inside a list comprehension, so
    a single undated observation raised TypeError and discarded every other
    record fetched for that location — up to 10,000 of them.
    """
    raw = result.get("observed_on")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_observation(result: dict) -> Observation:
    lat_str, lng_str = result["location"].split(",")
    lat, lng = float(lat_str), float(lng_str)

    taxon = result.get("taxon") or {}
    photos = result.get("photos") or []
    photo_url = photos[0]["url"] if photos else None
    # iNaturalist licenses the record and each photo separately — a CC0 record
    # can carry an all-rights-reserved photo, and vice versa.
    photo_license = photos[0].get("license_code") if photos else None
    user = result.get("user") or {}

    geoprivacy = result.get("geoprivacy") or "open"
    is_obscured = geoprivacy == "obscured"

    return Observation(
        observation_id=result["id"],
        species=taxon.get("name", "Unknown"),
        common_name=taxon.get("preferred_common_name"),
        taxon_id=taxon.get("id"),
        lat=lat,
        lng=lng,
        observed_on=_observed_date(result),
        quality_grade=result.get("quality_grade", ""),
        photo_url=photo_url,
        observer=user.get("login"),
        place_guess=result.get("place_guess"),
        jurisdiction=jurisdiction_for_coords(lat, lng),
        geoprivacy=geoprivacy,
        is_obscured=is_obscured,
        obscuration_radius_km=22.0 if is_obscured else None,
        license_code=result.get("license_code"),
        photo_license_code=photo_license,
        observer_id=user.get("id"),
        uri=result.get("uri"),
    )
