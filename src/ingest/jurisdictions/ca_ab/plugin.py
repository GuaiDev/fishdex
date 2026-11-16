"""Alberta (CA-AB) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_ab"
_INGEST = f"{_PKG}.ingest"
_LICENCE = "Open Government Licence – Alberta"

PLUGIN = JurisdictionPlugin(
    code="CA-AB",
    name="Alberta",
    country="CA",
    bbox=BBox(48.9, 60.1, -116.0, -110.0, priority=90),
    sources={
        "hydro_network": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_hydro_network",
            label="AB hydro network",
            licence=_LICENCE,
            url=(
                "https://geospatial.alberta.ca/titan/rest/services/fisheries/fwmis_hydrography/Featur"
                "eServer/0"
            ),
            note="FWMIS Simplified Hydro Arcs; barrier_count is always 0.",
        ),
        "fish_observations": SourceSpec(
            status="planned",
            note="AB FWMIS not publicly accessible.",
        ),
        "water_quality": SourceSpec(
            status="planned",
            runner=f"{_INGEST}:run_water_quality",
            label="AB water quality",
            note="Stub: no public API.",
        ),
        "stocking": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_stocking",
            area_scoped=False,
            label="AB stocking",
            ttl_days=365,
            ttl_constant=f"{_PKG}.stocking:_XLSX_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://open.alberta.ca/dataset/ae7521d6-7629-4b69-ac45-857fc798c10c",
        ),
        "regulations": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_regulations",
            area_scoped=False,
            label="AB regulations",
            ttl_days=365,
            ttl_constant=f"{_PKG}.regulations:_PDF_CACHE_TTL_SECONDS",
            licence=_LICENCE,
            url="https://open.alberta.ca/dataset/dbf392f4-266f-4947-adc0-fa4bdf4e2c9c",
        ),
        "species_ranges": SourceSpec(
            status="planned",
        ),
        "geology": SourceSpec(
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
            "Bow River Calgary",
            51.05,
            -114.07,
            50.0,
            sources=("global", "hydro_network", "stocking", "regulations", "water_quality"),
        ),
        CronArea(
            "North Saskatchewan River Edmonton",
            53.53,
            -113.49,
            50.0,
            sources=("global", "hydro_network", "stocking", "regulations", "water_quality"),
        ),
        CronArea(
            "Oldman River Lethbridge",
            49.7,
            -112.84,
            40.0,
            sources=("global", "hydro_network", "stocking", "regulations", "water_quality"),
        ),
    ),
    hydro_adapter=f"{_PKG}.hydro_network",
    regulations_adapter=f"{_PKG}.regulations",
    status_authorities=("COSEWIC", "SARA", "Alberta Wildlife Act"),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any AB lat/lng. "
        "HYDRO: FWMIS Simplified Hydro Arcs FeatureServer (fwmis_hydrography layer 0) — "
        "stream arcs with Strahler stream order, water-body id/name and length_m in metres; "
        "WGS84 in/out (inSR=outSR=4326, no reprojection needed); maxRecordCount 2000, "
        "paginated via resultOffset and the exceededTransferLimit flag; first ~7k segments "
        "verified live across the Bow/Elbow area 2026-10-09. No barrier layer — "
        "barrier_count is always 0 for CA-AB. REGULATIONS: only 3 Fish Management Zones "
        "split into 10 Watershed Units (ES1-4/PP1-2/NB1-4) — verified all 10 extract "
        "correctly against the 2026 edition (7.5k-31k chars each); URL resolved dynamically "
        "via CKAN package_show (picks the highest-year PDF resource) since a new dated file "
        "is added every year — see regulations.py. STOCKING: verified against 2026 edition, "
        "527 records — contrary to an earlier assumption the file DOES include lat/lng; "
        "species use AB's BKTR/RNTR/BNTR/TGTR/WSCT codes (mapped to full names); no clean "
        "stocking date, only free-text schedule notes (preserved in stocking_purpose) — see "
        "stocking.py. WATER QUALITY: AEMERA portal is map-based with no public API; "
        "DataStream (ca_national/) covers some AB watersheds."
    ),
)
