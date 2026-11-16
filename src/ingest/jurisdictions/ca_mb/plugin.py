"""Manitoba (CA-MB) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_mb"
_INGEST = f"{_PKG}.ingest"

PLUGIN = JurisdictionPlugin(
    code="CA-MB",
    name="Manitoba",
    country="CA",
    bbox=BBox(48.9, 60.1, -101.36, -88.9, priority=110),
    sources={
        "hydro_network": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_hydro_network",
            label="MB WaLLAS stream network",
            url="https://web43.gov.mb.ca/arcgis/rest/services/WALLAS/WaLLAS_REFERENCE/MapServer/18",
        ),
        "fish_observations": SourceSpec(
            status="planned",
            note="No provincial fish-observation layer.",
        ),
        "water_quality": SourceSpec(
            status="planned",
            note="Beach monitoring only; DataStream covers Lake Winnipeg.",
        ),
        "stocking": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_stocking",
            label="MB stocking",
            ttl_days=30,
            ttl_constant=f"{_PKG}.stocking:_CACHE_TTL_SECONDS",
            url=(
                "https://services.arcgis.com/mMUesHYPkXjaFGfS/arcgis/rest/services/Manitoba_Waterbody"
                "_Stocking_Records/FeatureServer/0"
            ),
        ),
        "regulations": SourceSpec(
            status="planned",
        ),
        "benthic": SourceSpec(
            status="implemented",
            runner="src.ingest.jurisdictions.ca_national.ingest:run_benthic",
            area_scoped=False,
            label="CABIN benthic",
            note="CABIN (federal; all provinces).",
        ),
    },
    cron_areas=(
        CronArea(
            "Red River Winnipeg",
            49.9,
            -97.14,
            50.0,
            sources=("global", "hydro_network", "stocking"),
        ),
        CronArea(
            "Lake Winnipeg South",
            50.7,
            -96.9,
            60.0,
            sources=("global", "hydro_network", "stocking"),
        ),
        CronArea(
            "Assiniboine River",
            50.07,
            -99.95,
            40.0,
            sources=("global", "hydro_network", "stocking"),
        ),
    ),
    hydro_adapter=f"{_PKG}.hydro_network",
    status_authorities=("COSEWIC", "SARA", "Manitoba Endangered Species and Ecosystems Act"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Manitoba. HYDRO: WaLLAS "
        "Reference 'Watercourses (NTS 1:20,000)' MapServer layer 18 — the province's "
        "queryable 1:20k network; a mixed feature class, so the adapter filters to F_TYPE "
        "LIKE 'RIVER/STREAM%' (excludes beaver dams). FEAT_NAME is blank for most segments "
        "and ORDER_NUM is often 0, so those fields are frequently NULL rather than "
        "fabricated. web43.gov.mb.ca is an older 10.51 ArcGIS Server and is occasionally "
        "slow. STOCKING: Manitoba Waterbody Stocking Records FeatureServer/0 — one point per "
        "event with real DATE, species, size and quantity; rows with no coordinates are kept "
        "with NULL lat/lng. WATER QUALITY: provincial stream/lake chemistry is by request; "
        "the only public layer is beach E. coli/microcystin monitoring (public-health, not "
        "habitat chemistry) — see ca_mb/water_quality.py. DataStream covers the Lake "
        "Winnipeg basin (ca_national/). FISH OBSERVATIONS: none public — Waterbody Data "
        "SPECIES field is empty and the Wildlife Atlas is internal."
    ),
)
