"""Per-area ingest used by `weekly-ingest` and `ingest-source`.

`run_area` runs one source for one area: the global bundle, or a source the
area's jurisdiction plugin declares (`src/jurisdictions/plugin.py`). Failures
are isolated so one flaky source does not stop the rest, and each run returns an
`AreaIngestResult` saying what was stored and what failed. See README.md for
refresh commands and area configuration.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from src.jurisdictions.plugin import GLOBAL_SOURCE, AreaContext, JurisdictionPlugin, SourceSpec
from src.jurisdictions.registry import all_plugins, get_plugin, plugins
from src.models.ingest_area import AreaIngestResult, IngestArea
from src.storage.database import ensure_schema, get_db

_log = logging.getLogger(__name__)


def plugin_areas() -> list[IngestArea]:
    """Every area the jurisdiction plugins schedule, in registry order."""
    return [
        IngestArea(
            label=a.label,
            jurisdiction=plugin.code,
            lat=a.lat,
            lng=a.lng,
            radius_km=a.radius_km,
            sources=list(a.sources),
            source_radius_km=dict(a.source_radius_km),
        )
        for plugin in all_plugins()
        for a in plugin.cron_areas
    ]


def load_areas(path: Path | None = None) -> list[IngestArea]:
    """The areas to ingest: those the plugins declare, or a JSON file when given.

    Raises on a malformed file — never skips.
    """
    if path is None:
        return plugin_areas()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [IngestArea.model_validate(a) for a in data["areas"]]


def _attempt(result: AreaIngestResult, name: str, fn: Callable[[], object]) -> None:
    """Run one dataset; record its count, or its error, on `result`."""
    from src.services.ingest_runs import ingest_run

    jurisdiction = {
        IngestSource.GLOBAL: "CA-ON", IngestSource.BC: "CA-BC",
        IngestSource.AB: "CA-AB", IngestSource.QC: "CA-QC",
        IngestSource.MB: "CA-MB", IngestSource.SK: "CA-SK",
    }.get(result.source)
    with ingest_run(name, jurisdiction=jurisdiction, area=result.label) as run:
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            _log.exception("[%s] %s fetch failed", result.label, name)
            result.failed[name] = f"{type(exc).__name__}: {exc}"
            run.error = result.failed[name]
            return
        if isinstance(value, dict):
            result.stored.update(value)
            count = sum(int(n or 0) for n in value.values())
        else:
            count = int(value or 0)
            result.stored[name] = count
        run.record(fetched=max(count, 1), stored=count, empty=1 if not count else 0)
        _log.info("[%s] %s: %s", result.label, name, value)


def run_global_ingest(
    lat: float, lng: float, radius_km: float, label: str, days_back: int | None = 90
) -> AreaIngestResult:
    """Run iNat, GBIF, WSC, OSM, and the SDM retrain check for a location."""
    from src.services.gbif import fetch_and_store as gbif_fetch
    from src.services.observations import fetch_and_store as inat_fetch
    from src.services.osm import fetch_and_store as osm_fetch
    from src.services.stream_gauge import fetch_and_store as wsc_fetch

    _log.info(
        "[%s] Global ingest started — lat=%.4f lng=%.4f radius=%.0fkm", label, lat, lng, radius_km
    )
    db = get_db()
    ensure_schema(db)
    result = AreaIngestResult(label=label, source=GLOBAL_SOURCE)

    _attempt(
        result,
        "iNaturalist",
        lambda: inat_fetch(lat, lng, radius_km=radius_km, days_back=days_back),
    )
    _attempt(result, "GBIF", lambda: gbif_fetch(lat, lng, radius_km=radius_km))
    _attempt(result, "WSC gauges", lambda: wsc_fetch(lat, lng, radius_km=radius_km))

    def _osm() -> dict[str, int]:
        water, access = osm_fetch(lat, lng)
        return {"OSM water features": water, "OSM access points": access}

    _attempt(result, "OSM", _osm)

    _check_sdm_retrain(db, label)
    _log.info("[%s] Global ingest complete", label)
    return result


def _check_sdm_retrain(db, label: str) -> None:
    """Log species whose trip-log presences grew ≥20% since training. Non-fatal."""
    try:
        import joblib

        from src.services.species_mapping import COMMON_TO_SCIENTIFIC

        model_dir = "data/processed/sdm_models"
        if not os.path.exists(model_dir):
            return
        trip_counts: dict = {}
        for (sc_json,) in db.execute(
            "SELECT species_caught FROM stops WHERE was_productive = 1"
        ).fetchall():
            for common in json.loads(sc_json or "[]"):
                clean = common.lower().replace("(uncertain)", "").strip()
                sci = COMMON_TO_SCIENTIFIC.get(clean)
                if sci:
                    trip_counts[sci] = trip_counts.get(sci, 0) + 1
        retrain_candidates = []
        for f in os.listdir(model_dir):
            if not f.endswith(".joblib"):
                continue
            b = joblib.load(os.path.join(model_dir, f))
            species = b.get("species")
            baseline = (b.get("n_inat", 0) or 0) + (b.get("n_gbif", 0) or 0)
            n_trip = b.get("n_trip_log", 0) or 0
            current = trip_counts.get(species, 0)
            if baseline > 0 and (current - n_trip) >= baseline * 0.20:
                retrain_candidates.append(species)
        if retrain_candidates:
            _log.info("[%s] SDM retrain recommended for: %s", label, retrain_candidates)
    except Exception:
        _log.exception("[%s] SDM retrain check failed", label)


class IngestJobError(ValueError):
    """A (jurisdiction, source, area) that cannot be run, with the reason and the options."""


def _sources_hint(plugin: JurisdictionPlugin) -> str:
    runnable = [n for n, spec in plugin.sources.items() if spec.runner]
    other = [n for n, spec in plugin.sources.items() if not spec.runner]
    text = f"{plugin.code} can run: {', '.join([GLOBAL_SOURCE, *runnable])}"
    if other:
        text += f" (declared but not runnable here: {', '.join(other)})"
    return text


def check_job(jurisdiction: str, source: str) -> SourceSpec | None:
    """Raise `IngestJobError` unless `source` can run for `jurisdiction`.

    Returns the plugin's `SourceSpec`, or None for the global bundle, which
    works anywhere.
    """
    if source == GLOBAL_SOURCE:
        return None
    plugin = get_plugin(jurisdiction)
    if plugin is None:
        known = ", ".join(sorted(plugins()))
        raise IngestJobError(f"unknown jurisdiction {jurisdiction!r} (known: {known})")
    spec = plugin.sources.get(source)
    if spec is None:
        raise IngestJobError(f"{plugin.code} has no source {source!r}. {_sources_hint(plugin)}")
    if not spec.runner:
        hint = f" {spec.note}" if spec.note else ""
        raise IngestJobError(f"{plugin.code} source {source!r} has no runner.{hint}")
    return spec


def run_area(area: IngestArea, source: str, days_back: int | None = 90) -> AreaIngestResult:
    """Run one source for one area: the global bundle, or a source its plugin declares."""
    spec = check_job(area.jurisdiction, source)
    radius = area.radius_for(source)
    if spec is None:
        return run_global_ingest(area.lat, area.lng, radius, area.label, days_back)

    _log.info(
        "[%s] %s %s started — lat=%.4f lng=%.4f radius=%.0fkm",
        area.label,
        area.jurisdiction,
        source,
        area.lat,
        area.lng,
        radius,
    )
    ensure_schema(get_db())
    result = AreaIngestResult(label=area.label, source=source)
    ctx = AreaContext(area.label, area.lat, area.lng, radius)
    _attempt(result, spec.label or source, lambda: spec.load_runner()(ctx))
    return result


def resolve_area(
    jurisdiction: str,
    source: str,
    area: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
) -> IngestArea:
    """The area a one-off `ingest-source` run covers.

    A configured area is picked by its label (case-insensitive, within the
    jurisdiction); otherwise `lat`/`lng` describe an ad-hoc one. A region-wide
    source (regulations, species ranges) needs neither and gets a placeholder
    its runner ignores.
    """
    spec = check_job(jurisdiction, source)
    if area:
        configured = [a for a in load_areas() if a.jurisdiction == jurisdiction]
        match = [a for a in configured if a.label.lower() == area.lower()]
        if not match:
            labels = ", ".join(a.label for a in configured) or "none configured"
            raise IngestJobError(f"{jurisdiction} has no area {area!r} (configured: {labels})")
        base = match[0]
        return base.model_copy(
            update={
                "radius_km": radius_km if radius_km is not None else base.radius_for(source),
                "sources": [source],
                "source_radius_km": {
                    **base.source_radius_km,
                    **({source: radius_km} if radius_km is not None else {}),
                },
            }
        )
    if lat is not None and lng is not None:
        return IngestArea(
            label=f"{jurisdiction} ({lat}, {lng})",
            jurisdiction=jurisdiction,
            lat=lat,
            lng=lng,
            radius_km=radius_km or 50.0,
            sources=[source],
        )
    if spec is not None and not spec.area_scoped:
        return IngestArea(
            label=f"{jurisdiction} {source}",
            jurisdiction=jurisdiction,
            lat=0.0,
            lng=0.0,
            radius_km=1.0,
            sources=[source],
        )
    raise IngestJobError("give --area LABEL, or --lat and --lng")
