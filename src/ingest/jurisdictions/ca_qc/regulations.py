"""Quebec sportfishing regulations — per-zone PDF ingestion.

Downloads one "Fishing periods, limits and exceptions" PDF per fishing zone
from the Ministère de l'Environnement, de la Lutte contre les changements
climatiques, de la Faune et des Parcs (MELCCFP) Regpec service, and stores
each PDF's full text as one chunk in regulation_chunks.

Source discovery
----------------
The printable-versions page lists every zone and links each to a PDF URL of
the form:

    https://peche.faune.gouv.qc.ca/regpec/en/Exporting/ExportGrillePdf
        ?idZone=<N>&idSaison=-1&courantes=False

⚠️ The idZone query parameter is NOT the printed zone number. Quebec renumbers
its internal ids so they drift from the published labels ("Zone 14" serves
?idZone=15, "Zone 20" serves ?idZone=22, "Zone 19 north" serves ?idZone=3063,
etc.), and the split sub-zones share a printed parent ("Zone 13 east"/"west"
under "Zone 13"). Hardcoding that mapping is exactly the discovery-failure
class that took PWQMN down (a publisher rename silently yielding zero records,
indistinguishable from a genuinely empty source). So this adapter re-derives
every (idZone, label) pair from the live page on each run and calls
check_resource_discovery to make a layout change loud. The zone↔idZone pairing
is the page's job, not ours.

Zone -> zone id
---------------
Each discovery entry becomes one chunk with a sequential integer zone id in
page document order (1..34 as of the 2026-2027 season: Zones 1-12, 13 east,
13 west, 14-18, 19 north, 19 south part A/B, 20-21, 22 north/south,
23 north/south, 24-29). zone_name carries the exact published label, e.g.
"Zone 13 east". Sequential ids mean a split parent never collides with its
sub-zones (the BC adapter uses the same trick with synthetic 71/72 for 7A/7B).

season year
-----------
regulation_year is parsed from each PDF's own season header
("April 1, 2026 to March 31, 2027" → 2026), so it auto-advances when
MELCCFP publishes the next season — no yearly code edit.

The province-wide general rules are on an HTML page, not a PDF, and are not
ingested here. Per-zone PDFs are the structured season/limit/exception data.

Table: regulation_chunks (shared schema)
  zone: sequential int in page document order; zone_name = published label.
  jurisdiction='CA-QC'
"""

import html as html_lib
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.ingest.discovery import check_resource_discovery

_PAGE_URL = (
    "https://www.quebec.ca/en/tourism-recreation-sport/"
    "sporting-and-outdoor-activities/sport-fishing/printable-versions"
)
# The Regpec PDF endpoint. idZone is discovered from _PAGE_URL each run.
_PDF_URL_TEMPLATE = (
    "https://peche.faune.gouv.qc.ca/regpec/en/Exporting/ExportGrillePdf"
    "?idZone={idzone}&idSaison=-1&courantes=False"
)
_CACHE_DIR = Path("data/cache/qc_regulations")
_PAGE_CACHE = _CACHE_DIR / "printable_versions.html"
# Regulations can be amended mid-season, and the page links the zones for the
# current season. Short TTL so a rule change lands within a week of the weekly
# ingest run — the PDFs themselves are small (~100-800 KB each).
_CACHE_TTL_SECONDS = 7 * 86400
_UA = "fishbot/1.0 (personal fishing exploration bot)"

# "Zone 13 east Fishing periods, limits and exceptions" -> ("Zone 13 east", 13)
_LINK_RE = re.compile(
    r"<a href=\"(https://peche\.faune\.gouv\.qc\.ca/regpec/en/Exporting/"
    r"ExportGrillePdf\?idZone=\d+)[^\"]*\"[^>]*>([^<]+)</a>"
)
_IDZONE_RE = re.compile(r"idZone=(\d+)")
_SUFFIX_RE = re.compile(r"\s*Fishing periods, limits and exceptions\s*$", re.IGNORECASE)
_SEASON_RE = re.compile(r"April 1,? (\d{4}) to March 31, (\d{4})")
_ZONE_TITLE_RE = re.compile(r"^Rules for the zone\s*\n(Zone .+)$", re.MULTILINE | re.IGNORECASE)

logger = logging.getLogger(__name__)


def fetch_regulations() -> list[dict]:
    """Discover zone PDF links from the printable-versions page, download each
    per-zone PDF, and return one row dict per zone for regulation_chunks.

    Returns an empty list on discovery failure (with a loud warning) — never
    guesses a zone mapping when the page is gone or renamed.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.error("QC regulations: pdfplumber not installed. Install with: uv add pdfplumber")
        return []

    entries, candidates = _discover_zone_links()
    if not entries:
        check_resource_discovery("QC regulations", 0, candidates, "ExportGrillePdf?idZone= links")
        logger.warning(
            "QC regulations: no zone PDF links discovered from %s", _PAGE_URL
        )
        return []

    chunks: list[dict] = []
    failures = 0
    for zone_id, label, url in entries:
        try:
            text = _extract_zone_pdf(url, zone_id)
            season_year = _season_year(text)
            chunks.append(
                {
                    "zone": zone_id,
                    "zone_name": label,
                    "jurisdiction": "CA-QC",
                    "regulation_year": season_year,
                    "raw_text": text.strip(),
                    "char_count": len(text.strip()),
                    "source_url": url,
                    "ingested_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            failures += 1
            logger.error("QC regulations: zone %s (%s) failed: %s", zone_id, label, exc)

    if failures:
        logger.warning(
            "QC regulations: %d of %d zones failed to extract — %d chunks written",
            failures, len(entries), len(chunks),
        )
    else:
        logger.info(
            "QC regulations: %d zone chunks extracted from %s",
            len(chunks), _PAGE_URL,
        )
    return chunks


def _discover_zone_links() -> tuple[list[tuple[int, str, str]], list[str]]:
    """Parse the printable-versions page into (zone_id, label, pdf_url) triples.

    zone_id is sequential in page document order; label is the exact published
    anchor text minus the "Fishing periods, limits and exceptions" suffix.
    Returns (entries, candidate_link_hrefs) where the latter feeds
    check_resource_discovery when nothing matches.
    """
    page = _fetch_page()
    if page is None:
        return [], []

    matches = list(_LINK_RE.finditer(page))
    candidates = [m.group(1) for m in matches]
    entries: list[tuple[int, str, str]] = []
    for i, m in enumerate(matches, start=1):
        idzone_m = _IDZONE_RE.search(m.group(1))
        if not idzone_m:
            continue
        label = _SUFFIX_RE.sub("", html_lib.unescape(m.group(2))).replace("\xa0", " ").strip()
        if not label.startswith("Zone"):
            logger.warning("QC regulations: unexpected zone anchor label %r, skipping", label)
            continue
        url = _PDF_URL_TEMPLATE.format(idzone=idzone_m.group(1))
        entries.append((i, label, url))
    return entries, candidates


def _fetch_page() -> str | None:
    """Return the printable-versions page HTML, cached on disk for _TTL."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _PAGE_CACHE.exists():
        age = time.time() - _PAGE_CACHE.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            logger.info("QC regulations: printable-versions page cache fresh, reusing")
            return _PAGE_CACHE.read_text()
    try:
        resp = httpx.get(
            _PAGE_URL,
            headers={"User-Agent": _UA},
            follow_redirects=True,
            timeout=60,
        )
        resp.raise_for_status()
        _PAGE_CACHE.write_text(resp.text)
        return resp.text
    except Exception as exc:
        logger.error("QC regulations: printable-versions page fetch failed: %s", exc)
        return None


def _extract_zone_pdf(url: str, zone_id: int) -> str:
    """Download one zone PDF (disk-cached) and return its full extracted text."""
    import pdfplumber

    path = _download_pdf_if_stale(url, zone_id)
    if path is None:
        raise RuntimeError("PDF download failed")
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _download_pdf_if_stale(url: str, zone_id: int) -> Path | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"zone_{zone_id}.pdf"
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            return path
    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": _UA},
            follow_redirects=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            with path.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
        logger.info("QC regulations: downloaded zone %d PDF (%.1f KB)", zone_id, path.stat().st_size / 1024)
        return path
    except Exception as exc:
        logger.error("QC regulations: zone %d PDF download failed: %s", zone_id, exc)
        return None


def _season_year(text: str) -> int:
    """Parse the season start year from the PDF header, e.g. 2026 from
    "(April 1, 2026 to March 31, 2027)". Fails loudly rather than guess."""
    m = _SEASON_RE.search(text)
    if not m:
        raise RuntimeError(f"no season header (April 1, YYYY …) found: {text[:200]!r}")
    return int(m.group(1))