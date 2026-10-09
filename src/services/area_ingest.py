"""Per-area ingest bundles — the one code path behind /ingest/data* and `weekly-ingest`.

Each `run_*_ingest` function runs every dataset in its bundle for one area,
isolating failures so one flaky source does not stop the rest, and returns an
`AreaIngestResult` saying what was stored and what failed. The API endpoints
run them as background tasks; the `weekly-ingest` CLI command runs them in
sequence over the areas listed in `data/ingest_areas.json`.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from src.models.ingest_area import AreaIngestResult, IngestArea, IngestSource
from src.storage.database import ensure_schema, get_db

_log = logging.getLogger(__name__)

DEFAULT_AREAS_PATH = Path("data/ingest_areas.json")


def load_areas(path: Path = DEFAULT_AREAS_PATH) -> list[IngestArea]:
    """Read and validate the area list. Raises on a malformed file — never skips."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [IngestArea.model_validate(a) for a in data["areas"]]


def _attempt(result: AreaIngestResult, name: str, fn: Callable[[], object]) -> None:
    """Run one dataset; record its count, or its error, on `result`."""
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - isolation is the point
        _log.exception("[%s] %s fetch failed", result.label, name)
        result.failed[name] = f"{type(exc).__name__}: {exc}"
        return
    if isinstance(value, dict):
        result.stored.update(value)
    else:
        result.stored[name] = int(value or 0)
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
    result = AreaIngestResult(label=label, source=IngestSource.GLOBAL)

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


def run_bc_ingest(lat: float, lng: float, radius_km: float, label: str) -> AreaIngestResult:
    """Run FWA, FISS, and BC EMS ingest for a BC location."""
    from src.services.bc_ingest import (
        ingest_bc_hydro_network,
        ingest_bc_water_quality,
        ingest_fiss_observations,
    )

    _log.info(
        "[%s] BC ingest started — lat=%.4f lng=%.4f radius=%.0fkm", label, lat, lng, radius_km
    )
    ensure_schema(get_db())
    result = AreaIngestResult(label=label, source=IngestSource.BC)
    _attempt(
        result,
        "FWA stream segments",
        lambda: ingest_bc_hydro_network(lat, lng, radius_km=radius_km)[0],
    )
    _attempt(
        result,
        "FISS observations",
        lambda: ingest_fiss_observations(lat, lng, radius_km=radius_km),
    )
    _attempt(
        result,
        "BC EMS water quality",
        lambda: ingest_bc_water_quality(lat, lng, radius_km=radius_km),
    )
    _log.info("[%s] BC ingest complete", label)
    return result


def run_ab_ingest(lat: float, lng: float, radius_km: float, label: str) -> AreaIngestResult:
    """Run Alberta-specific ingest adapters."""
    from src.services.ab_ingest import ingest_ab_data

    _log.info(
        "[%s] AB ingest started — lat=%.4f lng=%.4f radius=%.0fkm", label, lat, lng, radius_km
    )
    ensure_schema(get_db())
    result = AreaIngestResult(label=label, source=IngestSource.AB)
    _attempt(result, "AB adapters", lambda: ingest_ab_data(lat, lng, radius_km))
    return result


def run_qc_ingest(lat: float, lng: float, radius_km: float, label: str) -> AreaIngestResult:
    """Run Quebec-specific ingest adapters."""
    from src.services.qc_ingest import ingest_qc_data

    _log.info(
        "[%s] QC ingest started — lat=%.4f lng=%.4f radius=%.0fkm", label, lat, lng, radius_km
    )
    ensure_schema(get_db())
    result = AreaIngestResult(label=label, source=IngestSource.QC)
    _attempt(result, "QC adapters", lambda: ingest_qc_data(lat, lng, radius_km))
    return result


def run_tidal_ingest(lat: float, lng: float, radius_km: float, label: str) -> AreaIngestResult:
    """Run CHS tidal predictions ingest."""
    from src.ingest.jurisdictions.ca_national.tidal import fetch_tidal_readings

    _log.info(
        "[%s] Tidal ingest started — lat=%.4f lng=%.4f radius=%.0fkm", label, lat, lng, radius_km
    )
    db = get_db()
    ensure_schema(db)
    result = AreaIngestResult(label=label, source=IngestSource.TIDAL)

    def _tidal() -> int:
        rows = fetch_tidal_readings(lat, lng, radius_km)
        if rows:
            db["tidal_readings"].upsert_all(rows, pk="record_id")
        return len(rows)

    _attempt(result, "CHS tide predictions", _tidal)
    return result


_RUNNERS = {
    IngestSource.BC: run_bc_ingest,
    IngestSource.AB: run_ab_ingest,
    IngestSource.QC: run_qc_ingest,
    IngestSource.TIDAL: run_tidal_ingest,
}


def run_area(
    area: IngestArea, source: IngestSource, days_back: int | None = 90
) -> AreaIngestResult:
    """Run one source bundle for one configured area."""
    radius = area.radius_for(source)
    if source is IngestSource.GLOBAL:
        return run_global_ingest(area.lat, area.lng, radius, area.label, days_back)
    return _RUNNERS[source](area.lat, area.lng, radius, area.label)
