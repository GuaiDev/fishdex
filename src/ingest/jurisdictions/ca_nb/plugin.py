"""New Brunswick (CA-NB) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_nb"
_NATIONAL = "src.ingest.jurisdictions.ca_national.ingest"
_FEDERAL = "Open Government Licence – Canada"

PLUGIN = JurisdictionPlugin(
    code="CA-NB",
    name="New Brunswick",
    country="CA",
    bbox=BBox(45.0, 48.1, -69.1, -63.6, priority=120),
    sources={
        "hydro_network": SourceSpec(
            status="planned",
            note="No public WFS found.",
        ),
        "fish_observations": SourceSpec(
            status="planned",
        ),
        "water_quality": SourceSpec(
            status="planned",
            note="DataStream covers Atlantic Canada.",
        ),
        "stocking": SourceSpec(
            status="planned",
        ),
        "regulations": SourceSpec(
            status="planned",
            url="https://www2.gnb.ca/content/dam/gnb/Departments/nr-rn/pdf/en/Fish/Fish.pdf",
        ),
        "benthic": SourceSpec(
            status="implemented",
            runner="src.ingest.jurisdictions.ca_national.ingest:run_benthic",
            area_scoped=False,
            label="CABIN benthic",
            licence=_FEDERAL,
            note="CABIN (federal; all provinces).",
        ),
        "tidal": SourceSpec(
            status="implemented",
            runner=f"{_NATIONAL}:run_tidal",
            label="CHS tide predictions",
            ttl_days=1,
            ttl_constant="src.ingest.jurisdictions.ca_national.tidal:_PREDICTIONS_TTL",
            licence=_FEDERAL,
            url="https://api-sine.dfo-mpo.gc.ca/stations",
            note="CHS tidal API (ca_national/tidal.py) for coastal fishing.",
        ),
    },
    cron_areas=(
        CronArea(
            "Miramichi River NB",
            46.97,
            -65.54,
            50.0,
            sources=("global", "tidal"),
            source_radius_km={"tidal": 100.0},
        ),
        CronArea(
            "Saint John River NB",
            45.97,
            -66.64,
            50.0,
            sources=("global",),
        ),
    ),
    status_authorities=("COSEWIC", "SARA"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover New Brunswick. Miramichi is one "
        "of the world's best Atlantic salmon rivers — iNat + GBIF coverage good. CHS tidal "
        "API for tidal reach fishing (Miramichi tidal, Saint John tidal). DataStream covers "
        "Atlantic Canada watersheds."
    ),
)
