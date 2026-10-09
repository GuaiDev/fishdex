"""BC EnMoDS water quality ingestion (CA-BC).

Source: BC Environmental Monitoring Data System (EnMoDS), which replaced EMS on
2026-03-05 (EMS stopped receiving data on 2026-02-26). Dataset slug
"bc-environmental-monitoring-data-system-results" on the BC Data Catalogue.

The results are four time-tier files served by the COMS object API (no auth for
GET). This adapter reads only the current tier ("Current EnMoDS Results",
roughly the last two years):

    https://coms.api.gov.bc.ca/api/v1/object/84ed1220-bd51-40a8-9f29-d916144e2dfe

Measured 2026-10-09: the object 302-redirects to a short-lived signed URL, and the
payload is a single gzip stream of a CSV (413,529,081 bytes), not a zip. The
older tiers are out of scope — "is this water habitable now" needs the current
tier only.

HOW IT WORKS
  1. Download the .csv.gz once into the cache directory, streamed to disk in
     chunks, and reuse it for 30 days (the file is refreshed roughly monthly).
  2. Stream-decompress it row by row, never holding the file in memory.
  3. Keep rows whose Location_Latitude/Location_Longitude fall within radius_km
     of the query point. The file carries its own coordinates, so the old EMS
     station WFS is not needed — and it cannot miss locations that only exist
     in EnMoDS.
  4. Keep fresh-water, normal (non-blank/replicate/spike), detected, numeric
     results for the five parameters the water slice uses, and fold the rows
     of one visit into a single WaterQualityReading.

PARAMETER MATCHING
  By EMS observed-property code (the Observed_Property_Name column), not by the
  long label in Observed_Property_ID, which is free text. Codes and units were
  checked against the real file: DO 0014 (mg/L only — the % saturation rows
  share the code and are skipped on unit), pH 0004/PH-F, temperature 0013/TEMF,
  conductivity 0011/SC-F (µS/cm; mS/cm is converted), turbidity 0015/TURF (NTU,
  stored in the turbidity_fnu column — the two units agree for routine use).

Cache TTL: 30 days for the downloaded file.
"""

import csv
import gzip
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
from pydantic import ValidationError

from src.models.water_quality_reading import WaterQualityReading

_RESULTS_URL = "https://coms.api.gov.bc.ca/api/v1/object/84ed1220-bd51-40a8-9f29-d916144e2dfe"
_CACHE_DIR = Path("data/cache/bc_enmods")
_CACHE_FILE = "enmods_current.csv.gz"
_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
_CHUNK_BYTES = 1 << 20
_JURISDICTION = "CA-BC"

# EMS observed-property code -> (reading field, accepted units -> multiplier).
_PARAMETERS: dict[str, tuple[str, dict[str, float]]] = {
    "0014": ("do_mgl", {"mg/L": 1.0}),
    "0004": ("ph", {"pH units": 1.0}),
    "PH-F": ("ph", {"pH units": 1.0}),
    "0013": ("temp_c", {"degC": 1.0}),
    "TEMF": ("temp_c", {"degC": 1.0}),
    "0011": ("conductivity_us_cm", {"µS/cm": 1.0, "mS/cm": 1000.0}),
    "SC-F": ("conductivity_us_cm", {"µS/cm": 1.0, "mS/cm": 1000.0}),
    "0015": ("turbidity_fnu", {"NTU": 1.0}),
    "TURF": ("turbidity_fnu", {"NTU": 1.0}),
}

logger = logging.getLogger(__name__)


@dataclass
class ParseStats:
    """What the pass over the file kept and what it threw away, and why."""

    rows_scanned: int = 0
    rows_in_radius: int = 0
    rows_not_wanted: int = 0  # wrong medium, QC type, non-detect, other parameter
    rows_unusable: int = 0  # wanted, but value/unit/date/coordinate would not parse
    rows_rejected: int = 0  # parsed, but a reading validator refused the value
    readings: int = 0
    unit_counts: dict[str, int] = field(default_factory=dict)


def fetch_water_quality_readings(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[WaterQualityReading]:
    """Return BC EnMoDS readings within radius_km of lat/lng, newest data included.

    Downloads (or reuses) the current-tier file, then streams it. Raises on
    download failure — an unreachable source must not look like "no stations".
    """
    path = download_results()
    readings, stats = parse_results(path, lat, lng, radius_km)
    log = logger.warning if stats.rows_unusable + stats.rows_rejected else logger.info
    log(
        "BC EnMoDS: scanned %d rows, %d within %.0fkm, %d unusable, %d rejected by "
        "validators, %d not wanted -> %d readings",
        stats.rows_scanned,
        stats.rows_in_radius,
        radius_km,
        stats.rows_unusable,
        stats.rows_rejected,
        stats.rows_not_wanted,
        stats.readings,
    )
    return readings


# ── download ───────────────────────────────────────────────────────────────────


def download_results() -> Path:
    """Return the cached current-tier .csv.gz, downloading it if absent or stale."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / _CACHE_FILE
    if target.exists():
        age = time.time() - target.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            logger.info("BC EnMoDS file is fresh (%.1f days old), skipping download", age / 86400)
            return target

    part = target.with_suffix(".part")
    logger.info("Downloading BC EnMoDS current results (~400 MB) …")
    # The COMS object endpoint 302s to a short-lived signed URL, so redirects
    # must be followed. Write to a .part file so an interrupted download never
    # leaves a truncated file that looks fresh.
    with httpx.stream(
        "GET",
        _RESULTS_URL,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(60.0, read=300.0),
    ) as r:
        r.raise_for_status()
        with part.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=_CHUNK_BYTES):
                f.write(chunk)
    part.replace(target)
    logger.info("Downloaded BC EnMoDS results to %s (%d bytes)", target, target.stat().st_size)
    return target


# ── parse ──────────────────────────────────────────────────────────────────────


def parse_results(
    path: Path,
    lat: float,
    lng: float,
    radius_km: float,
) -> tuple[list[WaterQualityReading], ParseStats]:
    """Stream a results file (.csv or .csv.gz) and return (readings, stats)."""
    stats = ParseStats()
    visits: dict[tuple[str, str], dict] = {}
    deg_lat = radius_km / 111.0
    deg_lng = radius_km / (111.320 * math.cos(math.radians(lat)))

    with _open_text(path) as f:
        for row in csv.DictReader(f):
            stats.rows_scanned += 1
            coords = _coords(row)
            if coords is None:
                stats.rows_unusable += 1
                continue
            row_lat, row_lng = coords
            # Cheap box test first: this runs over millions of rows.
            if abs(row_lat - lat) > deg_lat or abs(row_lng - lng) > deg_lng:
                continue
            if _haversine_km(lat, lng, row_lat, row_lng) > radius_km:
                continue
            stats.rows_in_radius += 1
            _accumulate(row, row_lat, row_lng, visits, stats)

    readings: list[WaterQualityReading] = []
    for (location_id, observed), data in visits.items():
        try:
            readings.append(
                WaterQualityReading(
                    record_id=f"{_JURISDICTION}:{location_id}:{observed}",
                    station_id=location_id,
                    jurisdiction=_JURISDICTION,
                    **data,
                )
            )
        except ValidationError:
            stats.rows_rejected += 1
    stats.readings = len(readings)
    return readings, stats


def _accumulate(
    row: dict[str, str],
    row_lat: float,
    row_lng: float,
    visits: dict[tuple[str, str], dict],
    stats: ParseStats,
) -> None:
    spec = _PARAMETERS.get(row.get("Observed_Property_Name", "").strip())
    if (
        spec is None
        or row.get("Medium") != "Water - Fresh"
        or row.get("QC_Type") != "NORMAL"
        or row.get("Detection_Condition")
    ):
        stats.rows_not_wanted += 1
        return

    field_name, units = spec
    unit = row.get("Result_Unit", "")
    multiplier = units.get(unit)
    if multiplier is None:
        # DO as % saturation shares the DO code; anything else is unexpected.
        stats.rows_not_wanted += 1
        stats.unit_counts[unit] = stats.unit_counts.get(unit, 0) + 1
        return

    observed = row.get("Observed_Date_Time", "").strip()
    location_id = row.get("Location_ID", "").strip()
    try:
        value = float(row["Result_Value"]) * multiplier
        sampled_at = date.fromisoformat(observed[:10])
    except (KeyError, ValueError):
        stats.rows_unusable += 1
        return
    if not location_id or not math.isfinite(value):
        stats.rows_unusable += 1
        return

    visit = visits.setdefault(
        (location_id, observed),
        {
            "station_name": row.get("Location_Name") or None,
            "lat": row_lat,
            "lng": row_lng,
            "sampled_at": sampled_at,
        },
    )
    visit[field_name] = value


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open(encoding="utf-8-sig", newline="")


def _coords(row: dict[str, str]) -> tuple[float, float] | None:
    try:
        return float(row["Location_Latitude"]), float(row["Location_Longitude"])
    except (KeyError, ValueError):
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))
