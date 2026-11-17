"""PWQMN water quality field data ingestion for Ontario (CA-ON).

Downloads station coordinates and field data CSV files (DO, pH, temperature,
conductivity, turbidity) from the Provincial Water Quality Monitoring Network
published by Ontario MOE on data.ontario.ca (Open Government Licence – Ontario).

Field data resources are discovered via the CKAN package API and cached 30 days.
Individual CSV files use year-based TTLs: 365 days for completed past years,
7 days for the current year.
"""

import csv
import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

import httpx

from src.ingest.discovery import check_resource_discovery
from src.models.water_quality_reading import WaterQualityReading
from src.services.ingest_runs import record_error

_STATIONS_URL = "https://files.ontario.ca/moe_mapping/downloads/2Water/PWQMN/PWQMN_Stations.csv"
_CKAN_PACKAGE_URL = (
    "https://data.ontario.ca/api/3/action/package_show"
    "?id=provincial-stream-water-quality-monitoring-network"
)
_STATIONS_PATH = Path("data/raw/pwqmn_stations.csv")
_RAW_DIR = Path("data/raw")
_CACHE_DIR = Path("data/cache/pwqmn")
_STATIONS_TTL = 30 * 86400
_PACKAGE_TTL = 30 * 86400
_PAST_YEAR_TTL = 365 * 86400
_CURRENT_YEAR_TTL = 7 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# Earliest year we ingest, matching this module's documented "2021-present"
# scope. Ontario publishes chemistry back to 1964; pulling all of it is a
# separate decision, not an accident of string matching.
_MIN_DATA_YEAR = 2021

logger = logging.getLogger(__name__)


def download_stations() -> Path:
    """Download PWQMN station coordinates CSV. Skips if fresh (30-day TTL)."""
    if _STATIONS_PATH.exists():
        age = time.time() - _STATIONS_PATH.stat().st_mtime
        if age < _STATIONS_TTL:
            logger.info("PWQMN stations CSV is fresh (%.0f days old), skipping", age / 86400)
            return _STATIONS_PATH

    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading PWQMN stations CSV…")
    with httpx.stream(
        "GET",
        _STATIONS_URL,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    ) as r:
        r.raise_for_status()
        with _STATIONS_PATH.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
    logger.info("Downloaded PWQMN stations to %s", _STATIONS_PATH)
    return _STATIONS_PATH


def load_stations() -> dict[str, tuple[str | None, float | None, float | None]]:
    """Return {station_id: (name, lat, lng)} from the stations CSV."""
    stations: dict[str, tuple[str | None, float | None, float | None]] = {}
    path = download_stations()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sid = row.get("STATION", "").strip()
            if not sid:
                continue
            name = row.get("NAME", "").strip() or None
            lat_s = row.get("LATITUDE", "").strip()
            lng_s = row.get("LONGITUDE", "").strip()
            lat = float(lat_s) if lat_s else None
            lng = float(lng_s) if lng_s else None
            stations[sid] = (name, lat, lng)
    return stations


def _get_field_data_resources() -> list[dict]:
    """Query CKAN package API for Field Data resources. Returns [{name, url}]."""
    package_cache = _CACHE_DIR / "pwqmn_package.json"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if package_cache.exists() and time.time() - package_cache.stat().st_mtime < _PACKAGE_TTL:
        pkg = json.loads(package_cache.read_text())
    else:
        logger.info("Fetching PWQMN CKAN package metadata…")
        r = httpx.get(_CKAN_PACKAGE_URL, timeout=30, headers={"User-Agent": _USER_AGENT})
        r.raise_for_status()
        pkg = r.json()
        package_cache.write_text(json.dumps(pkg))

    resources = pkg.get("result", {}).get("resources", [])
    field_resources = []
    for res in resources:
        name = res.get("name", "")
        url = res.get("url", "")
        if url and _covers_recent_data(name, res.get("format", "")):
            field_resources.append({"name": name, "url": url})

    check_resource_discovery(
        source="PWQMN",
        matched=len(field_resources),
        candidates=[r.get("name", "") for r in resources],
        matcher=f"CSV resources covering {_MIN_DATA_YEAR} or later",
    )
    logger.info("Found %d PWQMN field data resources", len(field_resources))
    return field_resources


def _covers_recent_data(name: str, fmt: str) -> bool:
    """True for a CSV data file whose year range reaches _MIN_DATA_YEAR.

    Ontario names these by the period they cover — 'Data 2025',
    'Data March 2021 - 2024', 'Data 2019 - March 2021'. Matching on the years
    in the name rather than a fixed label survives the wording changing again,
    and it excludes the non-data resources ('Metadata', 'Coordinates',
    'Parameter Lookup') for free, since none of them carry a year.

    A range that merely *ends* inside the window still qualifies: the 2019-2021
    file holds the first quarter of 2021, and dropping it would leave a hole
    rather than a clean boundary.
    """
    if fmt and fmt.strip().upper() != "CSV":
        return False
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", name)]
    return bool(years) and max(years) >= _MIN_DATA_YEAR


def _label_for_resource(name: str) -> str:
    """Derive a filesystem-safe label from a resource name.

    e.g. 'Field Data 2024 English' → '2024'
    """
    # Extract year or year range patterns like "2024", "2021-2022", "2021_2022"
    match = re.search(r"\d{4}[-_]?\d{0,4}", name)
    if match:
        return match.group().replace("-", "_")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def download_field_data(resource_name: str, url: str) -> Path | None:
    """Download one field data CSV file. Returns path or None on failure."""
    label = _label_for_resource(resource_name)
    dest = _RAW_DIR / f"pwqmn_field_{label}.csv"

    current_year = datetime.now().year
    # Determine TTL: current year's file may update; past years are finalized
    ttl = _CURRENT_YEAR_TTL if str(current_year) in label else _PAST_YEAR_TTL

    if dest.exists():
        age = time.time() - dest.stat().st_mtime
        if age < ttl:
            logger.info(
                "PWQMN field data '%s' is fresh (%.0f days old), skipping",
                label,
                age / 86400,
            )
            return dest

    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading PWQMN field data '%s' from %s…", label, url)
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=120,
        ) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=65536):
                    f.write(chunk)
    except httpx.HTTPStatusError as exc:
        record_error(exc)
        logger.warning("PWQMN field data '%s' download failed: %s", label, exc)
        return None
    logger.info("Downloaded PWQMN field data '%s' to %s", label, dest)
    return dest


def _parse_float(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# Ontario has published collection dates in both US-style and ISO order across
# the archive; the long-format files use ISO. Tried in order, first match wins.
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def _parse_date(s: str) -> date | None:
    s = s.strip()
    if not s:
        return None
    # Long-format rows carry a timestamp on some exports; the date is enough.
    s = s.split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# Analyte labels in the long-format files, mapped to our reading fields. Ontario
# publishes several near-synonyms (both "pH" and "Field pH", both "Conductivity"
# and "Specific Conductance"); each maps to the same destination and the first
# non-null value for a sample wins.
_ANALYTE_FIELDS: dict[str, str] = {
    "dissolved oxygen": "do_mgl",
    "ph": "ph",
    "field ph": "ph",
    "water temperature": "temp_c",
    "temperature": "temp_c",
    "conductivity": "conductivity_us_cm",
    "specific conductance": "conductivity_us_cm",
    "turbidity": "turbidity_fnu",
    "sample turbidity": "turbidity_fnu",
}

# The pre-2021 exports are also long, but use legacy parameter CODES and legacy
# column names (STATION / PARM / DATE_YYYYMMDD) instead of the current
# Collection Site / Analyte / Collection Date.
_LEGACY_PARM_FIELDS: dict[str, str] = {
    "do": "do_mgl",
    "fwph": "ph",
    "ph": "ph",
    "fwtemp": "temp_c",
    "cond25": "conductivity_us_cm",
    "condam": "conductivity_us_cm",
    "turb": "turbidity_fnu",
    "turb (fnu)": "turbidity_fnu",
}

# Column aliases across both long variants: canonical name -> candidates.
_LONG_COLUMNS: dict[str, tuple[str, ...]] = {
    "site": ("collection site", "station"),
    "date": ("collection date", "date_yyyymmdd"),
    "parameter": ("analyte", "parm"),
    "value": ("result",),
}


def parse_field_data(
    csv_path: Path,
    stations: dict[str, tuple[str | None, float | None, float | None]],
) -> list[WaterQualityReading]:
    """Parse a PWQMN field data CSV into WaterQualityReading models.

    Two published layouts, detected from the header rather than the filename:

    * **long** (current) — one row per measurement, parameter in `Analyte` and
      value in `Result`. Rows are pivoted back into one reading per
      (site, date) sample.
    * **wide** (2021-2024 archives) — one row per sample with a column per
      parameter, in three column-naming variants.

    Ontario switched to long without renaming anything, so the wide parser
    silently matched no parameter columns and dropped every row. Sniffing the
    header keeps both eras readable.
    """
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        header = csv.DictReader(f).fieldnames or []
    normalized = {h.strip().lower() for h in header}

    is_long = "result" in normalized and ("analyte" in normalized or "parm" in normalized)
    if is_long:
        return _parse_long_format(csv_path, stations)
    return _parse_wide_format(csv_path, stations)


def _parse_long_format(
    csv_path: Path,
    stations: dict[str, tuple[str | None, float | None, float | None]],
) -> list[WaterQualityReading]:
    """Pivot one-row-per-measurement into one reading per (site, date)."""
    samples: dict[tuple[str, str], dict] = {}

    def pick(row: dict, key: str) -> str:
        for candidate in _LONG_COLUMNS[key]:
            if candidate in row:
                return row[candidate]
        return ""

    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        for raw in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            parameter = pick(row, "parameter").lower()
            field = _ANALYTE_FIELDS.get(parameter) or _LEGACY_PARM_FIELDS.get(parameter)
            if field is None:
                continue  # one of the 50+ parameters we do not model

            site = _normalize_station_id(pick(row, "site").lstrip("'"))
            sampled_at = _parse_date(pick(row, "date"))
            if not site or sampled_at is None:
                continue

            value = _parse_float(pick(row, "value"))
            if value is None:
                continue  # non-detects arrive as "<0.5" and carry no usable number

            key = (site, sampled_at.isoformat())
            sample = samples.setdefault(
                key,
                {
                    "record_id": f"{site}_{sampled_at.isoformat()}",
                    "station_id": site,
                    "sampled_at": sampled_at,
                },
            )
            sample.setdefault(field, value)

    records: list[WaterQualityReading] = []
    rejected = 0
    for sample in samples.values():
        name, lat, lng = stations.get(sample["station_id"], (None, None, None))
        try:
            records.append(
                WaterQualityReading(
                    record_id=sample["record_id"],
                    station_id=sample["station_id"],
                    station_name=name,
                    lat=lat,
                    lng=lng,
                    jurisdiction="CA-ON",
                    sampled_at=sample["sampled_at"],
                    do_mgl=sample.get("do_mgl"),
                    ph=sample.get("ph"),
                    temp_c=sample.get("temp_c"),
                    conductivity_us_cm=sample.get("conductivity_us_cm"),
                    turbidity_fnu=sample.get("turbidity_fnu"),
                )
            )
        except ValueError:
            # The published archive contains physically impossible values
            # (a pH of 17.58, for one). The model is right to refuse them, and
            # one bad sample must not discard the other thousands in the file.
            rejected += 1

    if rejected:
        logger.warning(
            "PWQMN %s: %d sample(s) rejected by validation (out-of-range values in "
            "the published data), %d kept",
            csv_path.name,
            rejected,
            len(records),
        )
    return records


def _parse_wide_format(
    csv_path: Path,
    stations: dict[str, tuple[str | None, float | None, float | None]],
) -> list[WaterQualityReading]:
    """One row per sample, one column per parameter (2021-2024 archives)."""
    records: list[WaterQualityReading] = []
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, raw_row in enumerate(reader, start=1):
            # Normalize: strip whitespace from keys/values, replace spaces with underscores
            row = {k.strip().replace(" ", "_"): v.strip() for k, v in raw_row.items() if k}
            try:
                record = _parse_row(i, row, stations)
            except Exception as exc:
                logger.warning("PWQMN row %d: skipping — %s", i, exc)
                continue
            if record is not None:
                records.append(record)
    return records


def _normalize_station_id(raw: str) -> str:
    """Zero-pad numeric station IDs to 11 digits to match PWQMN_Stations.csv codes."""
    raw = raw.strip()
    if raw.isdigit() and len(raw) < 11:
        return raw.zfill(11)
    return raw


def _parse_row(
    row_num: int,
    row: dict,
    stations: dict[str, tuple[str | None, float | None, float | None]],
) -> WaterQualityReading | None:
    # Build record_id: prefer Field_ID (2024 schema), else workorder+sample (2021-2023)
    field_id = row.get("Field_ID", "").strip()
    workorder = row.get("Lab_Workorder_ID", "").strip()
    sample_id = row.get("Lab_Sample_ID", "").strip()
    year_str = (row.get("YEAR") or row.get("Year", "")).strip()

    if field_id:
        record_id = f"{year_str}_{field_id}" if year_str else field_id
    elif workorder and sample_id:
        if year_str:
            record_id = f"{year_str}_{workorder}_{sample_id}"
        else:
            record_id = f"{workorder}_{sample_id}"
    else:
        logger.warning("PWQMN row %d: no usable ID fields, skipping", row_num)
        return None

    date_str = row.get("Collection_Date", "")
    sampled_at = _parse_date(date_str)
    if sampled_at is None:
        logger.warning("PWQMN row %d: unparseable Collection_Date %r, skipping", row_num, date_str)
        return None

    raw_station = row.get("Collection_Site", "").strip()
    station_id = _normalize_station_id(raw_station)
    name, lat, lng = stations.get(station_id, (None, None, None))

    do_mgl = _parse_float(row.get("Dissolved_Oxygen_mgl", ""))
    ph = _parse_float(row.get("Field_PH", ""))
    temp_c = _parse_float(row.get("Water_Temperature_C", ""))
    conductivity = _parse_float(row.get("Specific_Conductance_uS_cm_1", ""))
    turbidity = _parse_float(row.get("Turb_FNU", ""))

    if all(v is None for v in [do_mgl, ph, temp_c, conductivity, turbidity]):
        logger.warning("PWQMN row %d: all parameters null, skipping", row_num)
        return None

    return WaterQualityReading(
        record_id=record_id,
        station_id=station_id,
        station_name=name,
        lat=lat,
        lng=lng,
        jurisdiction="CA-ON",
        sampled_at=sampled_at,
        do_mgl=do_mgl,
        ph=ph,
        temp_c=temp_c,
        conductivity_us_cm=conductivity,
        turbidity_fnu=turbidity,
    )
