"""Nova Scotia (CA-NS) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_ns"
_NATIONAL = "src.ingest.jurisdictions.ca_national.ingest"
_FEDERAL = "Open Government Licence – Canada"

PLUGIN = JurisdictionPlugin(
    code="CA-NS",
    name="Nova Scotia",
    country="CA",
    bbox=BBox(43.3, 47.1, -66.5, -59.6, priority=140),
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
            url="https://novascotia.ca/fish",
            note="DFO federal + NS supplement.",
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
            "Annapolis River NS",
            44.98,
            -65.5,
            40.0,
            sources=("global", "tidal"),
            source_radius_km={"tidal": 100.0},
        ),
        CronArea(
            "Margaree River NS",
            46.17,
            -61.07,
            30.0,
            sources=("global",),
        ),
    ),
    status_authorities=("COSEWIC", "SARA"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Nova Scotia. CHS tidal API "
        "provides tide predictions for coastal fishing (ca_national/tidal.py). DataStream "
        "covers Atlantic Canada watersheds."
    ),
)
