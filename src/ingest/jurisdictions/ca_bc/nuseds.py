"""NuSEDS salmon escapement data for BC.

Downloads the DFO NuSEDS (New Salmon Escapement Database System) "All Areas
NuSEDS" XLSX file — 420,000+ population-year-species records going back to
the 1950s (most recent decades much denser).

Source:
  Open Government Portal dataset c48669a3-045b-400d-b730-48aafe8c5ee6
  https://open.canada.ca/data/en/dataset/c48669a3-045b-400d-b730-48aafe8c5ee6
  The actual attachment URL is dated (e.g. "All Areas NuSEDS_20260601.xlsx")
  and changes with each DFO release, so the URL is resolved dynamically via
  the CKAN package_show API rather than hardcoded (same pattern as
  ca_qc/species_ranges.py's _find_fish_geojson_url).

  Note: there is also an "All Areas Simplified Version" resource, but as of
  the 2026-06 release it is published as CSV (not XLSX despite the display
  name) and its schema drops GAZETTED_NAME/WATERSHED_CDE/POP_ID in favour of
  a leaner AREA/WATERBODY/POPULATION set — this adapter uses the full
  "All Areas NuSEDS" XLSX instead since it carries the richer field set our
  schema wants (POP_ID, GAZETTED_NAME, WATERSHED_CDE).

No region filtering is needed: "All Areas NuSEDS" AREA values are BC Pacific
Fishery Management Area codes (1-29, with sub-letters like "2E"/"29K") —
verified against the full 2026-06 export with no Yukon/transboundary codes
present (those live in a separate "Yukon and Transboundary NuSEDS" resource
this adapter does not fetch). Every row in this file is BC data.

Neither the full nor the simplified NuSEDS export includes stream
coordinates as of the 2026-06 release (no X/Y or LAT/LON columns) — an
older adapter version assumed lat/lng columns existed; they don't anymore.
stream_lat/stream_lng are always None until a future release restores them
or we join against another BC waterbody gazetteer.

Requirements:
  openpyxl (for XLSX reading). Install with: uv add openpyxl
  Without openpyxl this module returns 0 records with a clear warning.

Table: salmon_escapement
  record_id (PK) = "NUSEDS_{ACT_ID}" — ACT_ID is NuSEDS's own unique row ID
  (verified 0 duplicates across 421k rows; POP_ID+year+species has 518
  duplicate combinations from multiple run designations, so ACT_ID is the
  correct natural key, not a composite of the other fields).
  population_id, waterbody_name, gazetted_name, watershed_code,
  species, analysis_year, max_estimate, stream_lat, stream_lng,
  jurisdiction='CA-BC', source='NuSEDS'

  max_estimate prefers NATURAL_SPAWNERS_TOTAL (the literal "escapement" to
  spawning grounds) and falls back to TOTAL_RETURN_TO_RIVER when the natural
  spawner count isn't tracked for that record. Many rows (roughly 70% in the
  2026-06 export) carry no numeric estimate at all — ADULT_PRESENCE/
  JACK_PRESENCE is a presence/absence record instead. These rows are still
  kept (max_estimate=None) since presence itself is signal.

Cache TTL: 365 days for the downloaded XLSX (annual-ish release cadence);
30 days for the package metadata lookup so a new dated release is picked up
reasonably promptly without hammering the CKAN API on every ingest run.
"""

import logging
import time
from pathlib import Path

import httpx

from src.services.ingest_runs import record_error

_PACKAGE_URL = "https://open.canada.ca/data/api/action/package_show"
_PACKAGE_ID = "c48669a3-045b-400d-b730-48aafe8c5ee6"
_XLSX_PATH = Path("data/raw/nuseds_all_areas.xlsx")
_PACKAGE_CACHE_PATH = Path("data/cache/nuseds/package_meta.json")
_XLSX_CACHE_TTL_SECONDS = 365 * 86400
_PACKAGE_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

logger = logging.getLogger(__name__)


def fetch_salmon_escapement() -> list[dict]:
    """Download and parse the NuSEDS "All Areas NuSEDS" XLSX.

    Returns rows for the salmon_escapement table. Returns empty list if
    openpyxl is not installed, the current download URL can't be resolved,
    or the download fails.
    """
    try:
        import openpyxl  # type: ignore
    except ImportError:
        logger.warning(
            "NuSEDS: openpyxl not installed — cannot read XLSX files. "
            "Install with: uv add openpyxl. Returning 0 records."
        )
        return []

    xlsx_url = _find_xlsx_url()
    if not xlsx_url:
        logger.warning(
            "NuSEDS: could not resolve the current 'All Areas NuSEDS' XLSX URL "
            "from the CKAN package. Check "
            "https://open.canada.ca/data/en/dataset/c48669a3-045b-400d-b730-48aafe8c5ee6 "
            "for the current resource name/URL."
        )
        return []

    _download_xlsx_if_stale(xlsx_url)
    if not _XLSX_PATH.exists():
        logger.error("NuSEDS: XLSX not found at %s", _XLSX_PATH)
        return []

    logger.info("NuSEDS: parsing XLSX — %s", _XLSX_PATH)
    wb = openpyxl.load_workbook(_XLSX_PATH, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header_raw = next(rows_iter, None)
    if header_raw is None:
        logger.error("NuSEDS: XLSX has no header row")
        wb.close()
        return []

    header = [str(h or "").strip().upper() for h in header_raw]
    logger.info("NuSEDS: XLSX columns: %s", header)

    def col(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    idx = {
        "act_id": col("ACT_ID"),
        "pop_id": col("POP_ID"),
        "waterbody": col("WATERBODY"),
        "gazetted": col("GAZETTED_NAME"),
        "watershed": col("WATERSHED_CDE") or col("FWA_WATERSHED_CDE"),
        "species": col("SPECIES"),
        "year": col("ANALYSIS_YR"),
        "natural_spawners_total": col("NATURAL_SPAWNERS_TOTAL"),
        "total_return": col("TOTAL_RETURN_TO_RIVER"),
    }

    rows: list[dict] = []
    n_skipped = 0

    for raw_row in rows_iter:

        def get(field: str):
            i = idx[field]
            return raw_row[i] if i is not None else None

        act_id = get("act_id")
        pop_id = str(get("pop_id") or "").strip()
        species = str(get("species") or "").strip()

        try:
            year = int(get("year")) if get("year") is not None else None
        except (TypeError, ValueError):
            year = None

        if act_id is None or not pop_id or not species or year is None:
            n_skipped += 1
            continue

        estimate = get("natural_spawners_total")
        if estimate is None:
            estimate = get("total_return")
        try:
            estimate = int(estimate) if estimate is not None else None
        except (TypeError, ValueError):
            estimate = None

        waterbody = str(get("waterbody") or "").strip() or None
        gazetted = str(get("gazetted") or "").strip() or None
        watershed = str(get("watershed") or "").strip() or None

        rows.append(
            {
                "record_id": f"NUSEDS_{act_id}",
                "population_id": pop_id,
                "waterbody_name": waterbody,
                "gazetted_name": gazetted,
                "watershed_code": watershed,
                "species": species,
                "analysis_year": year,
                "max_estimate": estimate,
                "stream_lat": None,
                "stream_lng": None,
                "jurisdiction": "CA-BC",
                "source": "NuSEDS",
                "ingested_at": None,  # set by caller
            }
        )

    wb.close()
    logger.info(
        "NuSEDS: %d BC records extracted (%d rows skipped — missing ACT_ID/POP_ID/species/year)",
        len(rows),
        n_skipped,
    )
    return rows


def _find_xlsx_url() -> str | None:
    """Resolve the current dated 'All Areas NuSEDS' XLSX URL via CKAN package_show.

    Excludes the "Simplified Version" resource (published as CSV as of 2026-06
    with a leaner schema) and the regional-subset resources (Fraser/Johnstone
    Strait/North Coast/etc.) — we want the single full "All Areas NuSEDS.xlsx".
    """
    _PACKAGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    pkg = None
    if _PACKAGE_CACHE_PATH.exists():
        age = time.time() - _PACKAGE_CACHE_PATH.stat().st_mtime
        if age < _PACKAGE_CACHE_TTL_SECONDS:
            import json

            pkg = json.loads(_PACKAGE_CACHE_PATH.read_text())

    if pkg is None:
        try:
            resp = httpx.get(
                _PACKAGE_URL,
                params={"id": _PACKAGE_ID},
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            import json

            pkg = resp.json().get("result", {})
            _PACKAGE_CACHE_PATH.write_text(json.dumps(pkg))
        except Exception as exc:
            record_error(exc)
            logger.error("NuSEDS: CKAN package fetch failed: %s", exc)
            return None

    for resource in pkg.get("resources", []):
        name = (resource.get("name") or "").strip()
        name_lower = name.lower()
        fmt = (resource.get("format") or "").lower()
        url = resource.get("url") or ""
        if (
            name_lower.startswith("all areas nuseds")
            and "simplified" not in name_lower
            and fmt == "xlsx"
            and url.lower().endswith(".xlsx")
        ):
            logger.info("NuSEDS: resolved current XLSX resource: %s (%s)", name, url)
            return url

    return None


def _download_xlsx_if_stale(url: str) -> None:
    _XLSX_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _XLSX_PATH.exists():
        age = time.time() - _XLSX_PATH.stat().st_mtime
        if age < _XLSX_CACHE_TTL_SECONDS:
            logger.info("NuSEDS: XLSX cache fresh, skipping download")
            return
    logger.info("NuSEDS: downloading from Open Government Portal …")
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=300,
        ) as response:
            response.raise_for_status()
            with _XLSX_PATH.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
        logger.info("NuSEDS: downloaded %.1f MB", _XLSX_PATH.stat().st_size / 1_048_576)
    except Exception as exc:
        record_error(exc)
        logger.error("NuSEDS: download failed: %s", exc)
