"""BC Freshwater Fishing Regulations — PDF ingestion.

Downloads the BC Freshwater Fishing Regulations Synopsis PDF and splits it
into one text chunk per management region (Regions 1-6, 8, plus the split
Region 7A/7B).

Source:
  BC Ministry of Environment — 2025-2027 synopsis
  https://www2.gov.bc.ca/assets/gov/sports-recreation-arts-and-culture/
  outdoor-recreation/fishing-and-hunting/freshwater-fishing/fishing_synopsis.pdf
  (the old outdoor-recreation/freshwater-fishing-regulations/... URL 404s;
  find the current link at the BC fishing regulations landing page if this
  one goes stale too — filenames on this site are not dated/versioned so
  there's no reliable pattern to predict the next change).

BC's region scheme changed in the 2025-2027 synopsis: the historic Region 7
(Omineca-Peace) split into 7A (Omineca) and 7B (Peace), and a new Region 8
(Okanagan) was carved out (previously folded into Region 3/Thompson-Nicola).
zone is stored as an int PK, so 7A/7B map to synthetic ids 71/72 — zone_name
carries the real label ("Omineca", "Peace").

pdfplumber's extract_text() renders the Region 7A (Omineca) chapter's running
header with every character doubled (e.g. "RREEGGIIOONN 77AA -- OOmmiinneeccaa"),
apparently from an overlapping duplicate text layer in the source PDF present
only on those pages — every other region's header extracts cleanly. The
region-header pattern below tolerates single-character doubling (each
expected character optionally followed by itself) to catch this; matched
snippets are only de-doubled (collapse consecutive identical characters) as
a fallback when they fail to parse as-is, so legitimate double letters in
region names (Kootenay, Cariboo) are never touched.

Each region's header repeats as a running header on every page of its
chapter (verified: 5-8 repeats per region in the 2025-2027 synopsis) — chunks
are built by grouping consecutivally-matched same-region headers into one
run and taking the full span from the run's first match to the start of the
next differently-labelled match, not by naively slicing between every pair
of matches (the region name also appears scattered in the table of contents
and cross-references; picking the run with the largest resulting span per
region reliably selects the real chapter over those incidental mentions).

Table: regulation_chunks
  PK: (zone, jurisdiction, regulation_year)
  zone is the Region number (1-6, 8) or synthetic 71/72 for 7A/7B;
  zone_name is the Region name.

Cache TTL: 365 days (biennial-ish BC synopsis; the actual cadence has
drifted before, so this is a ceiling not a promise — check data.bc.gov.ca
if a re-ingest returns suspiciously old-looking content).
"""

import logging
import re
import time
from pathlib import Path

import httpx

from src.services.ingest_runs import record_error

_PDF_URL = (
    "https://www2.gov.bc.ca/assets/gov/sports-recreation-arts-and-culture/"
    "outdoor-recreation/fishing-and-hunting/freshwater-fishing/fishing_synopsis.pdf"
)
_REG_YEAR = 2025
_PDF_PATH = Path("data/raw/bc_fishing_regulations_2025.pdf")
_CACHE_TTL_SECONDS = 365 * 86400
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"

# zone_key (as captured by _STRICT_HEADER, e.g. "7A") -> (int zone id, display name)
_REGIONS: dict[str, tuple[int, str]] = {
    "1": (1, "Vancouver Island"),
    "2": (2, "Lower Mainland"),
    "3": (3, "Thompson-Nicola"),
    "4": (4, "Kootenay"),
    "5": (5, "Cariboo"),
    "6": (6, "Skeena"),
    "7A": (71, "Omineca"),
    "7B": (72, "Peace"),
    "8": (8, "Okanagan"),
}
_REGION_NAMES = [
    "Vancouver Island",
    "Lower Mainland",
    "Thompson-Nicola",
    "Thompson Nicola",
    "Kootenay",
    "Cariboo",
    "Skeena",
    "Omineca",
    "Peace",
    "Okanagan",
]

logger = logging.getLogger(__name__)


def _doubling_tolerant(word: str) -> str:
    """Build a regex fragment matching `word` with any subset of its characters
    doubled (each character optionally followed by a repeat of itself)."""
    return "".join(re.escape(c) + ".?" for c in word)


# Locates candidate headers, tolerant of the per-character doubling artifact.
_HEADER_LOCATOR = re.compile(
    _doubling_tolerant("REGION")
    + r"\s*.?\d.?[AB]?.?\s*.?[-—–:].?\s*.?("
    + "|".join(_doubling_tolerant(n) for n in _REGION_NAMES)
    + ")",
    re.IGNORECASE,
)
# Parses a clean (or de-doubled) snippet into (zone_num, zone_letter, region_name).
_STRICT_HEADER = re.compile(
    r"REGION\s+(\d+)\s*([AB])?\s*[-—–:]?\s*(" + "|".join(re.escape(n) for n in _REGION_NAMES) + ")",
    re.IGNORECASE,
)


def fetch_regulations() -> list[dict]:
    """Download BC fishing regulations PDF and split into per-region chunks.

    Returns list of row dicts for regulation_chunks table.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.error("BC regulations: pdfplumber not installed. Install with: uv add pdfplumber")
        return []

    _download_pdf_if_stale()
    if not _PDF_PATH.exists():
        logger.error("BC regulations: PDF not found at %s", _PDF_PATH)
        return []

    logger.info("BC regulations: parsing PDF %s", _PDF_PATH)
    try:
        with pdfplumber.open(_PDF_PATH) as pdf:
            full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as exc:
        record_error(exc)
        logger.error("BC regulations: PDF parse failed: %s", exc)
        return []

    chunks = _split_by_region(full_text)
    logger.info("BC regulations: %d region chunks extracted", len(chunks))
    return chunks


def _collapse_doubled(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def _parse_header(snippet: str) -> tuple[str, str, str] | None:
    """Return (zone_key, zone_num, region_name) from a matched header snippet.

    Tries the snippet as-is first — this is what almost every region header
    hits, and never risks corrupting legitimate double letters (Kootenay,
    Cariboo). Only falls back to collapsing doubled characters when the plain
    snippet doesn't parse (the Region 7A/Omineca doubling artifact).
    """
    m = _STRICT_HEADER.search(snippet) or _STRICT_HEADER.search(_collapse_doubled(snippet))
    if not m:
        return None
    zone_num = m.group(1)
    zone_letter = (m.group(2) or "").upper()
    return zone_num + zone_letter, zone_num, m.group(3)


def _split_by_region(text: str) -> list[dict]:
    """Split regulation text into one chunk per BC management region."""
    import datetime

    now = datetime.datetime.utcnow().isoformat()

    matches = []
    for m in _HEADER_LOCATOR.finditer(text):
        parsed = _parse_header(m.group(0))
        if parsed is None:
            continue
        zone_key, _zone_num, _name = parsed
        matches.append({"start": m.start(), "zone_key": zone_key})

    if not matches:
        logger.warning("BC regulations: could not split by region — writing full text as zone 0")
        return [
            {
                "zone": 0,
                "zone_name": "All Regions",
                "jurisdiction": "CA-BC",
                "regulation_year": _REG_YEAR,
                "raw_text": text.strip(),
                "char_count": len(text.strip()),
                "source_url": _PDF_URL,
                "ingested_at": now,
            }
        ]

    # Group consecutive same-region matches into runs (running headers repeat
    # on every page of a chapter; the region name also appears in the TOC and
    # in incidental cross-references elsewhere, each producing a separate
    # short run for the same zone_key).
    runs: list[dict] = []
    for mm in matches:
        if runs and runs[-1]["zone_key"] == mm["zone_key"]:
            runs[-1]["starts"].append(mm["start"])
        else:
            runs.append({"zone_key": mm["zone_key"], "starts": [mm["start"]]})

    all_starts = [mm["start"] for mm in matches]

    # For each zone_key, keep whichever run yields the largest resulting span
    # — the real chapter, not an incidental single-line mention.
    best: dict[str, tuple[int, int, int]] = {}  # zone_key -> (span, start, end)
    for run in runs:
        zone_key = run["zone_key"]
        start = run["starts"][0]
        last_start = run["starts"][-1]
        i = all_starts.index(last_start)
        end = all_starts[i + 1] if i + 1 < len(all_starts) else len(text)
        span = end - start
        if zone_key not in best or span > best[zone_key][0]:
            best[zone_key] = (span, start, end)

    chunks: list[dict] = []
    for zone_key, (_span, start, end) in best.items():
        zone_id, zone_name = _REGIONS.get(zone_key, (None, None))
        if zone_id is None:
            logger.warning("BC regulations: unrecognized region key %r, skipping", zone_key)
            continue
        chunk_text = text[start:end].strip()
        chunks.append(
            {
                "zone": zone_id,
                "zone_name": zone_name,
                "jurisdiction": "CA-BC",
                "regulation_year": _REG_YEAR,
                "raw_text": chunk_text,
                "char_count": len(chunk_text),
                "source_url": _PDF_URL,
                "ingested_at": now,
            }
        )

    return sorted(chunks, key=lambda c: c["zone"])


def _download_pdf_if_stale() -> None:
    _PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _PDF_PATH.exists():
        age = time.time() - _PDF_PATH.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            logger.info("BC regulations: PDF cache fresh, skipping download")
            return
    logger.info("BC regulations: downloading PDF …")
    try:
        response = httpx.get(
            _PDF_URL,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=120,
        )
        response.raise_for_status()
        _PDF_PATH.write_bytes(response.content)
        logger.info("BC regulations: downloaded %.1f MB", len(response.content) / 1_048_576)
    except Exception as exc:
        record_error(exc)
        logger.error("BC regulations: download failed: %s", exc)
