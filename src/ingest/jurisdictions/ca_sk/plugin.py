"""Saskatchewan (CA-SK) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_sk"
_INGEST = f"{_PKG}.ingest"
_LICENCE = "Open Government Licence – Saskatchewan"

PLUGIN = JurisdictionPlugin(
    code="CA-SK",
    name="Saskatchewan",
    country="CA",
    bbox=BBox(48.9, 60.1, -110.0, -101.36, priority=100),
    sources={
        "hydro_network": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_hydro_network",
            label="SK hydro network",
            ttl_days=90,
            ttl_constant=f"{_PKG}.hydro_network:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://gis.saskatchewan.ca/arcgis/rest/services/Hydrography/MapServer/121",
        ),
        "fish_observations": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_fish_observations",
            label="SK Fish Atlas observations",
            ttl_days=30,
            ttl_constant=f"{_PKG}.fish_observations:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/3",
        ),
        "water_quality": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_water_quality",
            label="SK water quality",
            ttl_days=90,
            ttl_constant=f"{_PKG}.water_quality:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url=(
                "https://services3.arcgis.com/zcv98lgAl8xQ04cW/arcgis/rest/services/LongTermWaterChem"
                "istry/FeatureServer/0"
            ),
        ),
        "stocking": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_stocking",
            label="SK stocking",
            ttl_days=30,
            ttl_constant=f"{_PKG}.stocking:_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://gis.saskatchewan.ca/arcgis/rest/services/Fisheries/FeatureServer/4",
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
            "South Saskatchewan River Saskatoon",
            52.13,
            -106.67,
            50.0,
            sources=("global", "hydro_network", "fish_observations", "stocking", "water_quality"),
        ),
        CronArea(
            "Qu Appelle River",
            50.44,
            -103.82,
            40.0,
            sources=("global", "hydro_network", "fish_observations", "stocking", "water_quality"),
        ),
        CronArea(
            "Churchill River",
            55.75,
            -108.45,
            50.0,
            sources=("global", "hydro_network", "fish_observations", "stocking", "water_quality"),
        ),
    ),
    hydro_adapter=f"{_PKG}.hydro_network",
    status_authorities=("COSEWIC", "SARA", "Saskatchewan Wild Species at Risk Regulations"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Saskatchewan. HYDRO: SaskGIS "
        "Hydrography MapServer 121 (NHN-derived watercourse network); tiers 107/115/121/123 "
        "return the same data. Stream order is not published, so stream_order is NULL (use "
        "OSM/width for order elsewhere). FISH OBSERVATIONS: SK Fish Atlas — "
        "FishAtlasHabiSask (FeatureServer/3, no geometry) joined to Species Info points "
        "(FeatureServer/1) via NRCANCODE→LOCATIONID. No observation date exists, so "
        "observed_on uses the shared 1900-01-01 sentinel. STOCKING: Fish Stocking History "
        "(FeatureServer/4, no geometry) joined the same way; STOCKING_DATE reaches back "
        "before 1970, so epoch values can be negative. WATER QUALITY: Long Term Water "
        "Chemistry — historical (1920s–1990s) lake/stream chemistry; most fields are text "
        "with NA/ND/Trace sentinels. No temperature column (temp_c NULL); conductivity is "
        "µmho/cm == µS/cm; Turbidity is Jackson units (JTU ≠ FNU) so turbidity_fnu is left "
        "NULL rather than mis-united."
    ),
)
