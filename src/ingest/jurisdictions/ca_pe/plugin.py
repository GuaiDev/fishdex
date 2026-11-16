"""Prince Edward Island (CA-PE) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_pe"
_NATIONAL = "src.ingest.jurisdictions.ca_national.ingest"
_FEDERAL = "Open Government Licence – Canada"

PLUGIN = JurisdictionPlugin(
    code="CA-PE",
    name="Prince Edward Island",
    country="CA",
    bbox=BBox(45.9, 47.1, -64.5, -61.9, priority=130),
    sources={
        "hydro_network": SourceSpec(
            status="planned",
            note="OSM adequate for PEI's small streams.",
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
            url="https://www.dfo-mpo.gc.ca/fisheries-peches/regs/index-eng.html",
            note="DFO federal regulations apply.",
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
    cron_areas=(),
    status_authorities=("COSEWIC", "SARA"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover PEI. PEI has no large freshwater "
        "systems — tidal and coastal fishing dominate. DFO federal regulations apply for "
        "most species."
    ),
)
