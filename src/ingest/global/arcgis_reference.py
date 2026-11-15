"""Generic ArcGIS REST reference-layer adapter, for the coverage check.

Most Canadian provincial portals and many US state ones publish their data as
ArcGIS REST layers, so one adapter reads any of them: give it the layer URL, a
bounding box and a `where` clause, and it pages through the answer. Which layer,
which fields and which filter all live in the region's registry file
(`data/coverage/references_*.toml`), never here.

Every fetch goes through a file cache. The cache key covers the layer, the box,
the filter and the fields, so changing any of them is a miss, never a wrong hit.

Two ways a fetch can lie, both made loud here:

- ArcGIS answers HTTP 200 with `{"error": {...}}` when a query is wrong. That
  is raised, not read as "no features".
- A renamed field makes every feature fail to parse. If features came back and
  none could be read, that is raised too; if only some could not, the count is
  returned and logged at WARNING. A reference that quietly shrinks makes recall
  look better than it is.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src.models.coverage import ReferenceFeature, ReferenceSpec

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/arcgis_reference")
CACHE_TTL_SECONDS = 2_592_000  # 30 days: reference inventories change slowly
PAGE_SIZE = 1000
MAX_PAGES = 200  # a runaway loop is a bug, not a big layer
_USER_AGENT = "fishbot/1.0 (personal fishing exploration bot; coverage check)"


class ReferenceFetchError(RuntimeError):
    """The service answered, but not with usable features."""


@dataclass
class ReferenceFetch:
    """What a reference fetch returned, and what it had to leave out."""

    features: list[ReferenceFeature]
    raw_count: int  # features the service returned
    skipped: int = 0  # returned but unreadable (no id, no point geometry)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    from_cache: bool = False


def fetch_reference(
    spec: ReferenceSpec,
    bbox: tuple[float, float, float, float],
    *,
    cache_dir: Path | None = None,
    ttl_seconds: int = CACHE_TTL_SECONDS,
    client: httpx.Client | None = None,
    now: float | None = None,
) -> ReferenceFetch:
    """Fetch a registry reference layer inside `bbox` and reduce it to points.

    `client` lets a test hand in a transport with a recorded response; nothing
    else about the call changes.
    """
    raw, from_cache = _fetch_raw(
        spec.service_url,
        bbox,
        spec.where,
        cache_dir=cache_dir or CACHE_DIR,
        ttl_seconds=ttl_seconds,
        client=client,
        now=now,
    )
    result = parse_features(raw, spec)
    result.from_cache = from_cache
    return result


def parse_features(raw: list[dict], spec: ReferenceSpec) -> ReferenceFetch:
    """Reduce raw ArcGIS JSON features to points, counting what could not be read."""
    out = ReferenceFetch(features=[], raw_count=len(raw))
    seen: set[str] = set()
    for feat in raw:
        attrs = feat.get("attributes") or {}
        ref_id = attrs.get(spec.id_field)
        if ref_id is None or ref_id == "":
            _skip(out, f"no {spec.id_field}")
            continue
        point = _point_of(feat.get("geometry"))
        if point is None:
            _skip(out, "no point geometry")
            continue
        key = str(ref_id)
        if key in seen:
            _skip(out, "duplicate id")
            continue
        seen.add(key)
        name = attrs.get(spec.name_field) if spec.name_field else None
        out.features.append(
            ReferenceFeature(
                ref_id=key,
                name=(str(name).strip() or None) if name is not None else None,
                lat=point[1],
                lon=point[0],
                attributes=attrs,
            )
        )
    if out.raw_count and not out.features:
        raise ReferenceFetchError(
            f"{spec.label}: the service returned {out.raw_count} features and none could be "
            f"read ({out.skipped_reasons}). The registry's id_field {spec.id_field!r} or the "
            "layer's geometry type probably does not match the layer."
        )
    if out.skipped:
        logger.warning(
            "%s: %d of %d reference features could not be read and are not counted: %s",
            spec.label,
            out.skipped,
            out.raw_count,
            out.skipped_reasons,
        )
    return out


# ── internal ────────────────────────────────────────────────────────────────


def _skip(out: ReferenceFetch, reason: str) -> None:
    out.skipped += 1
    out.skipped_reasons[reason] = out.skipped_reasons.get(reason, 0) + 1


def _point_of(geom: dict | None) -> tuple[float, float] | None:
    """(lon, lat) of a point geometry; None for anything else."""
    if not geom:
        return None
    x, y = geom.get("x"), geom.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if -180 <= x <= 180 and -90 <= y <= 90:
            return float(x), float(y)
    return None


def _cache_path(
    cache_dir: Path, service_url: str, bbox: tuple[float, float, float, float], where: str
) -> Path:
    key = json.dumps([service_url, [round(v, 6) for v in bbox], where, "4326"])
    return cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"


def _fetch_raw(
    service_url: str,
    bbox: tuple[float, float, float, float],
    where: str,
    *,
    cache_dir: Path,
    ttl_seconds: int,
    client: httpx.Client | None,
    now: float | None,
) -> tuple[list[dict], bool]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, service_url, bbox, where)
    clock = now if now is not None else time.time()
    if path.exists() and clock - path.stat().st_mtime < ttl_seconds:
        return json.loads(path.read_text())["features"], True

    own_client = client is None
    http = client or httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=60)
    try:
        features = _query_all_pages(http, service_url, bbox, where)
    finally:
        if own_client:
            http.close()
    # A failed cache write raises: a permanently cold cache would otherwise show
    # up only as a slow check and a hammered public service.
    path.write_text(json.dumps({"service_url": service_url, "features": features}))
    return features, False


def _query_all_pages(
    http: httpx.Client,
    service_url: str,
    bbox: tuple[float, float, float, float],
    where: str,
) -> list[dict]:
    url = service_url.rstrip("/") + "/query"
    features: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        params = {
            "geometry": ",".join(str(v) for v in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE),
        }
        resp = http.get(url, params=params)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise ReferenceFetchError(f"{service_url}: service error {body['error']}")
        page = body.get("features", [])
        features.extend(page)
        # `exceededTransferLimit` is the service saying "there is more"; a short
        # page without it is the end. Either rule alone mis-ends on some servers.
        if not page or (len(page) < PAGE_SIZE and not body.get("exceededTransferLimit")):
            return features
        offset += len(page)
    raise ReferenceFetchError(f"{service_url}: more than {MAX_PAGES} pages; refusing to continue")
