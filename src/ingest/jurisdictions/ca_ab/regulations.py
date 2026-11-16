"""Alberta sportfishing regulations — PDF ingestion.

Downloads the current-year Alberta Guide to Sportfishing Regulations PDF and
splits it into one text chunk per Watershed Unit — the actual regulation
subdivision this guide uses. There are only 3 top-level Fish Management
Zones (Eastern Slopes, Parkland-Prairie, Northern Boreal), each split into
4/2/4 Watershed Units respectively (ES1-ES4, PP1-PP2, NB1-NB4; 10 total) —
much coarser than the ~100 WMU scheme an earlier version of this docstring
assumed. Each Watershed Unit's "<CODE> WATERSHED UNIT REGULATIONS" header
appears exactly once in the source PDF (verified against the 2026 edition,
112 pages) — no running-header-per-page repeats to merge/dedupe, unlike the
BC regulations adapter.

Source:
  Open Alberta dataset dbf392f4-266f-4947-adc0-fa4bdf4e2c9c
  https://open.alberta.ca/publications/alberta-guide-to-sportfishing-regulations
  A new dated resource (e.g. "fp-alberta-guide-sportfishing-regulations-2026.pdf")
  is added to this same dataset every year, so the URL is resolved dynamically
  via the CKAN package_show API rather than hardcoded (same pattern as
  ca_bc/nuseds.py and ca_qc/species_ranges.py) — picks the PDF resource whose
  name contains the highest year.

Some pages (particularly the ones right before the ES2/ES3/PP2/NB2/NB3/NB4
headers, which back onto a watershed map graphic) extract with reversed/
rotated text ("eeluoC eniP fo maertsnwod" = "downstream of Pine Coulee"
backwards) — this is map-label text pdfplumber pulls out in the wrong reading
order, not a problem with the actual regulation body text, which reads
correctly. No attempt is made to un-reverse it (same accepted-artifact
category as the BC regulations adapter's Omineca header doubling).

Table: regulation_chunks (shared schema)
  zone: sequential int 1-10 in document order (ES1=1 … ES4=4, PP1=5, PP2=6,
  NB1=7 … NB4=10); zone_name e.g. "Eastern Slopes — ES1".
  jurisdiction='CA-AB'

Cache TTL: 365 days (annual release).
"""

import logging
import re
import time
from pathlib import Path

import httpx

from src.services.ingest_runs import record_error

_PACKAGE_URL = "https://open.alberta.ca/api/3/action/package_show"
_PACKAGE_ID = "dbf392f4-266f-4947-adc0-fa4bdf4e2c9c"
_PDF_PATH = Path("data/raw/ab_regulations.pdf")
_PACKAGE_CACHE_PATH = Path("data/cache/ab_regulations/package_meta.json")
_PDF_CACHE_TTL_SECONDS = 365 * 86400
_PACKAGE_CACHE_TTL_SECONDS = 30 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

_ZONE_FULL_NAME = {
    "ES": "Eastern Slopes",
    "PP": "Parkland-Prairie",
    "NB": "Northern Boreal",
}
# Document order -> sequential int zone id.
_ZONE_ORDER = ["ES1", "ES2", "ES3", "ES4", "PP1", "PP2", "NB1", "NB2", "NB3", "NB4"]
_ZONE_ID = {code: i + 1 for i, code in enumerate(_ZONE_ORDER)}

_UNIT_HEADER = re.compile(r"(ES|PP|NB)([0-9]+)\s+WATERSHED UNIT REGULATIONS", re.IGNORECASE)

logger = logging.getLogger(__name__)


def fetch_regulations() -> list[dict]:
    """Download the current Alberta sportfishing regulations PDF and split by
    Watershed Unit. Returns list of row dicts for regulation_chunks table.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.error("AB regulations: pdfplumber not installed. Install with: uv add pdfplumber")
        return []

    resolved = _find_current_pdf()
    if not resolved:
        logger.warning(
            "AB regulations: could not resolve current PDF URL from the CKAN package. "
            "Check https://open.alberta.ca/publications/alberta-guide-to-sportfishing-regulations "
            "for the current resource name/URL."
        )
        return []
    pdf_url, reg_year = resolved

    _download_pdf_if_stale(pdf_url)
    if not _PDF_PATH.exists():
        logger.error("AB regulations: PDF not found at %s", _PDF_PATH)
        return []

    logger.info("AB regulations: parsing PDF %s", _PDF_PATH)
    try:
        with pdfplumber.open(_PDF_PATH) as pdf:
            full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as exc:
        record_error(exc)
        logger.error("AB regulations: PDF parse failed: %s", exc)
        return []

    chunks = _split_by_watershed_unit(full_text, pdf_url, reg_year)
    logger.info("AB regulations: %d watershed unit chunks extracted", len(chunks))
    return chunks


def _split_by_watershed_unit(text: str, pdf_url: str, reg_year: int) -> list[dict]:
    import datetime

    now = datetime.datetime.utcnow().isoformat()
    matches = list(_UNIT_HEADER.finditer(text))

    if not matches:
        logger.warning(
            "AB regulations: could not split by watershed unit — writing full text as zone 0"
        )
        return [
            {
                "zone": 0,
                "zone_name": "All Zones",
                "jurisdiction": "CA-AB",
                "regulation_year": reg_year,
                "raw_text": text.strip(),
                "char_count": len(text.strip()),
                "source_url": pdf_url,
                "ingested_at": now,
            }
        ]

    chunks: list[dict] = []
    for i, m in enumerate(matches):
        group, num = m.group(1).upper(), m.group(2)
        code = f"{group}{num}"
        zone_id = _ZONE_ID.get(code)
        if zone_id is None:
            logger.warning("AB regulations: unrecognized watershed unit %r, skipping", code)
            continue

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()

        chunks.append(
            {
                "zone": zone_id,
                "zone_name": f"{_ZONE_FULL_NAME[group]} — {code}",
                "jurisdiction": "CA-AB",
                "regulation_year": reg_year,
                "raw_text": chunk_text,
                "char_count": len(chunk_text),
                "source_url": pdf_url,
                "ingested_at": now,
            }
        )

    return sorted(chunks, key=lambda c: c["zone"])


def _find_current_pdf() -> tuple[str, int] | None:
    """Resolve the current-year PDF URL + year via CKAN package_show.

    Picks the PDF-format resource whose name contains the highest 4-digit
    year (falls back to the most-recently-created resource on a tie).
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
            logger.error("AB regulations: CKAN package fetch failed: %s", exc)
            return None

    best: tuple[int, str, str] | None = None  # (year, created, url)
    for resource in pkg.get("resources", []):
        fmt = (resource.get("format") or "").lower()
        if fmt != "pdf":
            continue
        name = resource.get("name") or ""
        year_match = re.search(r"(20\d{2})", name)
        if not year_match:
            continue
        year = int(year_match.group(1))
        created = resource.get("created") or ""
        url = resource.get("url") or ""
        if not url:
            continue
        if best is None or (year, created) > (best[0], best[1]):
            best = (year, created, url)

    if best is None:
        return None
    year, _created, url = best
    logger.info("AB regulations: resolved %d edition: %s", year, url)
    return url, year


def _download_pdf_if_stale(url: str) -> None:
    _PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _PDF_PATH.exists():
        age = time.time() - _PDF_PATH.stat().st_mtime
        if age < _PDF_CACHE_TTL_SECONDS:
            logger.info("AB regulations: PDF cache fresh, skipping download")
            return
    logger.info("AB regulations: downloading PDF …")
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=120,
        ) as response:
            response.raise_for_status()
            with _PDF_PATH.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
        logger.info("AB regulations: downloaded %.1f MB", _PDF_PATH.stat().st_size / 1_048_576)
    except Exception as exc:
        record_error(exc)
        logger.error("AB regulations: download failed: %s", exc)
