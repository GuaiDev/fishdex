"""Probe Ontario Conservation Authority data portals and report what they hold.

This is discovery, not ingestion. It writes no fish records. It answers one
question per authority — "is there a machine-readable catalogue here, and does
it publish fish data?" — and writes a report.

The split matters for cost. Running this is free: plain HTTP, no model calls,
so it can sit on a cron forever and Ontario coverage grows without spending
anything. Writing an adapter for a newly-found source is the part that needs
judgment, and it only becomes worth doing once this reports a hit. So the
expensive step is demand-driven rather than done 34 times up front against
sources that may not exist.

Reads data/registry/ca_portals.json, which is config rather than code: adding
a portal URL is a one-line edit.

The three outcomes are kept distinct on purpose, because they have different
remedies:

    needs_url       nobody has recorded a website — a human must find one
    no_catalogue    the site responds but exposes no machine-readable index
    catalogue_found an index responds; its datasets are listed in the report

"The authority publishes nothing" and "we have not looked properly" are
different facts, and collapsing them is how a gap becomes invisible.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from src.ingest.discovery import check_resource_discovery

logger = logging.getLogger(__name__)

_REGISTRY = Path("data/registry/ca_portals.json")
_REPORT = Path("data/processed/ca_harvest_report.json")
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot)"
_TIMEOUT = 20.0
_POLITE_DELAY = 1.0

# Catalogue shapes worth probing, in order of how much they give us.
_PROBES: tuple[tuple[str, str], ...] = (
    ("ckan", "/api/3/action/package_list"),
    ("arcgis_hub", "/api/feed/dcat-us/1.1.json"),
)

# A dataset is a candidate if its name suggests fish or aquatic monitoring.
_FISH_KEYWORDS = ("fish", "aquatic", "electrofish", "rwmp", "benthic", "species")


@dataclass
class AuthorityResult:
    name: str
    status: str
    website: str | None = None
    catalogues: list[dict[str, Any]] = field(default_factory=list)
    fish_datasets: list[str] = field(default_factory=list)
    total_datasets: int = 0
    adapter: str | None = None
    error: str | None = None


def _candidate_hosts(website: str) -> list[str]:
    """The site itself plus a `data.` subdomain, which is where CKAN usually
    lives (data.trca.ca). Bounded deliberately — guessing more hostnames means
    hammering third-party domains to little purpose."""
    host = website.rstrip("/")
    bare = host.split("://", 1)[-1]
    out = [host]
    if not bare.startswith("data."):
        scheme = host.split("://", 1)[0] if "://" in host else "https"
        out.append(f"{scheme}://data.{bare}")
    return out


def _probe_catalogue(base: str) -> dict[str, Any] | None:
    """Return catalogue info if `base` exposes a machine-readable index."""
    for kind, path in _PROBES:
        url = f"{base}{path}"
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
        except Exception:
            continue
        if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
            continue
        try:
            payload = resp.json()
        except Exception:
            continue

        if kind == "ckan" and isinstance(payload, dict) and isinstance(payload.get("result"), list):
            return {"kind": kind, "url": base, "datasets": [str(d) for d in payload["result"]]}
        if kind == "arcgis_hub" and isinstance(payload, dict) and payload.get("dataset"):
            titles = [str(d.get("title", "")) for d in payload["dataset"]]
            return {"kind": kind, "url": base, "datasets": titles}
    return None


def harvest(registry_path: Path | None = None) -> list[AuthorityResult]:
    """Probe every authority in the registry. Returns one result each."""
    path = registry_path or _REGISTRY
    doc = json.loads(path.read_text(encoding="utf-8"))
    results: list[AuthorityResult] = []

    for entry in doc.get("authorities", []):
        name = entry.get("name", "?")
        website = entry.get("website")
        adapter = entry.get("adapter")

        if adapter:
            results.append(
                AuthorityResult(name=name, status="adapted", website=website, adapter=adapter)
            )
            continue

        bases: list[str] = [p["url"] for p in entry.get("portals", []) if p.get("url")]
        if website:
            bases.extend(h for h in _candidate_hosts(website) if h not in bases)

        if not bases:
            results.append(AuthorityResult(name=name, status="needs_url"))
            continue

        found = None
        for base in bases:
            found = _probe_catalogue(base)
            time.sleep(_POLITE_DELAY)
            if found:
                break

        if not found:
            results.append(AuthorityResult(name=name, status="no_catalogue", website=website))
            continue

        datasets = found["datasets"]
        fish = [d for d in datasets if any(k in d.lower() for k in _FISH_KEYWORDS)]
        # If a catalogue is sitting right there and the keyword filter matches
        # nothing, that is a fact about our filter as much as about the source.
        check_resource_discovery(
            source=f"CA harvest: {name}",
            matched=len(fish),
            candidates=datasets,
            matcher=f"dataset name contains any of {_FISH_KEYWORDS}",
        )
        results.append(
            AuthorityResult(
                name=name,
                status="catalogue_found",
                website=website,
                catalogues=[{"kind": found["kind"], "url": found["url"]}],
                fish_datasets=fish,
                total_datasets=len(datasets),
            )
        )
    return results


def write_report(results: list[AuthorityResult], report_path: Path | None = None) -> Path:
    """Write the report and log a summary.

    The summary is the point: it is the only output that should ever consume
    attention, and it says which authorities are newly worth adapting.
    """
    path = report_path or _REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    actionable = [r for r in results if r.status == "catalogue_found" and r.fish_datasets]
    doc = {
        "harvested_on": date.today().isoformat(),
        "summary": by_status,
        "actionable": [
            {"name": r.name, "catalogues": r.catalogues, "fish_datasets": r.fish_datasets}
            for r in actionable
        ],
        "authorities": [
            {
                "name": r.name,
                "status": r.status,
                "website": r.website,
                "adapter": r.adapter,
                "catalogues": r.catalogues,
                "total_datasets": r.total_datasets,
                "fish_datasets": r.fish_datasets,
                "error": r.error,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    logger.info("CA harvest: %s", ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    if actionable:
        logger.warning(
            "CA harvest: %d authority(ies) publish fish data with no adapter yet: %s",
            len(actionable),
            "; ".join(f"{r.name} ({len(r.fish_datasets)} datasets)" for r in actionable),
        )
    n_needs_url = by_status.get("needs_url", 0)
    if n_needs_url:
        logger.info(
            "CA harvest: %d authority(ies) have no website recorded and were not "
            "probed. They are not known to publish nothing — they are unchecked. "
            "Add a `website` to data/registry/ca_portals.json to include them.",
            n_needs_url,
        )
    return path
