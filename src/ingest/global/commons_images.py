"""Wikimedia Commons client for the species-image audit.

Commons is the only route to Duane Raver's US Fish and Wildlife Service
illustrations that answers programmatic requests: the FWS digital library and
fws.gov sit behind a bot filter that returns 403. Every Raver file on Commons
links back to its FWS record and carries a PD-USGov-FWS tag, which is the
public-domain statement the project accepts.

Read-only metadata lookups: category listings, file search, and per-file
imageinfo. No image bytes are downloaded here. All HTTP responses are cached to
data/cache/commons/ with a 7-day TTL, and uncached requests are spaced 0.5s
apart and back off on HTTP 429.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from src.models.commons_file import PD_USGOV_TEMPLATES, CommonsFile, strip_html

_API_URL = "https://commons.wikimedia.org/w/api.php"
_PAGE_URL = "https://commons.wikimedia.org/wiki/"
_CACHE_DIR = Path("data/cache/commons")
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_USER_AGENT = "fishdex-image-audit/0.1 (personal project; https://github.com/kunchenguid)"
_BATCH = 15
_FWS_RECORD_RE = re.compile(r'href="(https?://digitalmedia\.fws\.gov/[^"]+)"')


def _cached_get(params: dict[str, Any]) -> dict:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < _CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())

    data = _get_with_backoff({**params, "format": "json", "formatversion": 2})
    cache_file.write_text(json.dumps(data))
    return data


def _get_with_backoff(params: dict[str, Any]) -> dict:
    """GET the API: wait out 429s as Commons asks (Retry-After), retry dropped connections."""
    for attempt in range(8):
        time.sleep(0.5)
        try:
            response = httpx.get(
                _API_URL, params=params, headers={"User-Agent": _USER_AGENT}, timeout=30
            )
        except httpx.TransportError:
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()
        time.sleep(min(float(response.headers.get("Retry-After", 5 * (attempt + 1))), 60))
    raise RuntimeError(f"Commons API did not answer after 8 attempts: {params.get('action')}")


def category_files(category: str) -> list[str]:
    """Titles of the files directly inside a Commons category (empty if it does not exist)."""
    data = _cached_get(
        {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmtype": "file",
            "cmlimit": "500",
        }
    )
    return [m["title"] for m in data.get("query", {}).get("categorymembers", [])]


def search_files(query: str, limit: int = 20) -> list[str]:
    """Titles of files in the File namespace matching a full-text query."""
    data = _cached_get(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 6,
            "srlimit": limit,
        }
    )
    return [h["title"] for h in data.get("query", {}).get("search", [])]


def file_records(
    titles: list[str], templates: tuple[str, ...] = PD_USGOV_TEMPLATES
) -> dict[str, CommonsFile]:
    """Size and licence metadata for each title; missing titles are omitted.

    `templates` limits which Commons templates are reported per file, which is
    how the caller asks 'does this page carry tag X' without paging through
    every template on it.
    """
    out: dict[str, CommonsFile] = {}
    unique = sorted(set(titles))
    for i in range(0, len(unique), _BATCH):
        for title, record in _fetch_batch(unique[i : i + _BATCH], templates).items():
            out[title] = record
    return out


def _fetch_batch(titles: list[str], templates: tuple[str, ...]) -> dict[str, CommonsFile]:
    params: dict[str, Any] = {
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo|templates|categories",
        "iiprop": "url|size|mime|extmetadata",
        # Only the public-domain tags matter; listing every template on a
        # Commons page runs to dozens per file and trips the rate limit.
        "tltemplates": "|".join(f"Template:{t}" for t in templates),
        "tllimit": "max",
        "cllimit": "max",
    }
    pages: dict[str, dict] = {}
    while True:
        data = _cached_get(params)
        for page in data.get("query", {}).get("pages", []):
            merged = pages.setdefault(page["title"], {"templates": [], "categories": []})
            merged["templates"] += [t["title"] for t in page.get("templates", [])]
            merged["categories"] += [c["title"] for c in page.get("categories", [])]
            # Commons pages through a file's older revisions with `continue`; the
            # first imageinfo is the current version and must not be overwritten.
            if "imageinfo" in page and "imageinfo" not in merged:
                merged["imageinfo"] = page["imageinfo"][0]
        if "continue" not in data:
            break
        params = {**params, **data["continue"]}

    return {title: _parse_file(title, page) for title, page in pages.items() if "imageinfo" in page}


def _parse_file(title: str, page: dict) -> CommonsFile:
    info = page["imageinfo"]
    meta = info.get("extmetadata", {})

    def field(name: str) -> str:
        return strip_html(meta.get(name, {}).get("value"))

    fws_links = _FWS_RECORD_RE.findall(meta.get("Credit", {}).get("value", ""))
    # The credit box links the FWS library root first, then the specific record.
    fws_url = next((u for u in fws_links if "/id/" in u), None)
    return CommonsFile(
        title=title,
        page_url=_PAGE_URL + title.replace(" ", "_"),
        original_url=info["url"].split("?")[0],
        width=info["width"],
        height=info["height"],
        mime=info["mime"],
        artist=field("Artist"),
        description=field("ImageDescription"),
        licence_short=field("LicenseShortName"),
        fws_source_url=fws_url,
        templates=[t.removeprefix("Template:") for t in page["templates"]],
        categories=[c.removeprefix("Category:") for c in page["categories"]],
    )


def licence_statement(title: str) -> str:
    """The licence sentence the Commons file page itself displays, as plain text.

    Read from the rendered page so the audit records the words on the source
    page rather than a licence name inferred from a template.
    """
    data = _cached_get({"action": "parse", "page": title, "prop": "text"})
    html = data.get("parse", {}).get("text", "")
    start = html.find("licensetpl")
    if start == -1:
        return ""
    text = strip_html(html[start:])
    # The template body ends where the "For more information" pointer begins.
    sentence = re.search(r"(This (?:image|file|work).*?)(?: For more information|$)", text)
    return sentence.group(1).strip() if sentence else ""


def download_original(url: str, dest: Path) -> bytes:
    """Download one original image to `dest` untouched, unless it is already there.

    The originals folder is this call's cache: an existing file is returned as
    is, so a rerun never refetches and never rewrites a byte.
    """
    if dest.exists():
        return dest.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error = "no attempt made"
    for attempt in range(8):
        # The image host throttles bursts harder than the API does; pace downloads.
        time.sleep(15)
        try:
            response = httpx.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=120, follow_redirects=True
            )
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code == 429:
            last_error = "HTTP 429"
            time.sleep(float(response.headers.get("Retry-After", 60 * (attempt + 1))))
            continue
        response.raise_for_status()
        dest.write_bytes(response.content)
        return response.content
    raise RuntimeError(f"Could not download {url} after 8 attempts (last: {last_error})")
