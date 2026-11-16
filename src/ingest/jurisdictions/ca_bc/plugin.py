"""British Columbia (CA-BC) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_bc"
_INGEST = f"{_PKG}.ingest"
_LICENCE = "Open Government Licence – British Columbia"
_FEDERAL = "Open Government Licence – Canada"

PLUGIN = JurisdictionPlugin(
    code="CA-BC",
    name="British Columbia",
    country="CA",
    bbox=BBox(48.2, 60.1, -139.1, -116.0, priority=80),
    sources={
        "hydro_network": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_hydro_network",
            label="FWA stream segments",
            ttl_days=30,
            ttl_constant=f"{_PKG}.hydro_network:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://openmaps.gov.bc.ca/geo/pub/WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP/ows",
        ),
        "fish_observations": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_fish_observations",
            label="FISS observations",
            ttl_days=7,
            ttl_constant=f"{_PKG}.fish_observations:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://openmaps.gov.bc.ca/geo/pub/WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP/ows",
        ),
        "water_quality": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_water_quality",
            label="BC EnMoDS water quality",
            ttl_days=30,
            ttl_constant=f"{_PKG}.water_quality:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url=(
                "https://openmaps.gov.bc.ca/geo/pub/WHSE_ENVIRONMENTAL_MONITORING.EMS_MONITORING_LOCN"
                "_TYPES_SVW/ows"
            ),
        ),
        "stocking": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_stocking",
            label="FISS stocking",
            licence=_LICENCE,
            note="Derived from FISS via ACTIVITY_CODE filter; no quantity data.",
        ),
        "regulations": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_regulations",
            area_scoped=False,
            label="BC regulations",
            ttl_days=365,
            ttl_constant=f"{_PKG}.regulations:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url=(
                "https://www2.gov.bc.ca/assets/gov/sports-recreation-arts-and-culture/outdoor-recreat"
                "ion/fishing-and-hunting/freshwater-fishing/fishing_synopsis.pdf"
            ),
        ),
        "salmon_escapement": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_salmon_escapement",
            area_scoped=False,
            label="NuSEDS salmon escapement",
            ttl_days=365,
            ttl_constant=f"{_PKG}.nuseds:_XLSX_CACHE_TTL_SECONDS",
            licence=_FEDERAL,
            url="https://open.canada.ca/data/en/dataset/c48669a3-045b-400d-b730-48aafe8c5ee6",
        ),
        "species_ranges": SourceSpec(
            status="planned",
        ),
        "geology": SourceSpec(
            status="planned",
            note="BC surficial geology adapter not yet built.",
        ),
        "benthic": SourceSpec(
            status="implemented",
            runner="src.ingest.jurisdictions.ca_national.ingest:run_benthic",
            area_scoped=False,
            label="CABIN benthic",
            licence=_FEDERAL,
            note="CABIN (federal; all provinces).",
        ),
    },
    cron_areas=(
        CronArea(
            "Fraser River Lower Mainland",
            49.12,
            -122.05,
            60.0,
            sources=("global", "hydro_network", "fish_observations", "water_quality"),
        ),
        CronArea(
            "Thompson River Kamloops",
            50.67,
            -120.33,
            50.0,
            sources=("global", "hydro_network", "fish_observations", "water_quality"),
        ),
        CronArea(
            "Okanagan Vernon",
            50.27,
            -119.27,
            40.0,
            sources=("global", "hydro_network", "fish_observations", "water_quality"),
        ),
        CronArea(
            "Skeena River Terrace",
            54.51,
            -128.6,
            50.0,
            sources=("global", "hydro_network", "fish_observations", "water_quality"),
        ),
    ),
    hydro_adapter=f"{_PKG}.hydro_network",
    regulations_adapter=f"{_PKG}.regulations",
    status_authorities=("COSEWIC", "SARA", "BC Conservation Data Centre"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any BC lat/lng. "
        "STOCKING: derived from FISS via ACTIVITY_CODE filter — no quantity data available. "
        "SALMON_ESCAPEMENT: NuSEDS 'All Areas NuSEDS' XLSX (421k records verified 2026-06), "
        "requires openpyxl; URL resolved dynamically via CKAN package_show since the dated "
        "attachment filename changes every release — see nuseds.py. No stream coordinates in "
        "the source data as of the 2026-06 release. REGULATIONS: 2025-2027 synopsis now has "
        "9 regions (7 split into 7A/Omineca + 7B/Peace, new 8/Okanagan) — zone stores 71/72 "
        "for the split pair; verified all 9 extract correctly (39k-58k chars each) despite a "
        "PDF text-layer artifact that doubles every character in Region 7A's running header "
        "— see regulations.py. WATER QUALITY: stations WFS works; results still stubbed. "
        "Source system migrated EMS->EnMoDS 2026-03 (old EMS results resource ID was "
        "stale/dead) — see water_quality.py TODO for current EnMoDS resource URLs and sizes."
    ),
)
