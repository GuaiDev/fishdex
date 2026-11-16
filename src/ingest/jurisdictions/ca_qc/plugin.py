"""Quebec (CA-QC) jurisdiction manifest."""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_PKG = "src.ingest.jurisdictions.ca_qc"
_INGEST = f"{_PKG}.ingest"

PLUGIN = JurisdictionPlugin(
    code="CA-QC",
    name="Quebec",
    country="CA",
    bbox=BBox(44.9, 62.6, -79.8, -57.1, priority=150),
    sources={
        "hydro_network": SourceSpec(
            status="planned",
            note="RHN adapter not yet built; OSM covers QC rivers.",
        ),
        "fish_observations": SourceSpec(
            status="planned",
            note="Faune Québec not publicly accessible.",
        ),
        "water_quality": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_water_quality",
            label="QC water quality",
            ttl_days=30,
            ttl_constant=f"{_PKG}.water_quality:_PACKAGE_TTL",
        ),
        "stocking": SourceSpec(
            status="planned",
            note="Not publicly available.",
        ),
        "regulations": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_regulations",
            area_scoped=False,
            label="QC regulations",
            url=(
                "https://www.quebec.ca/en/tourism-recreation-sport/sporting-and-outdoor-activities/sp"
                "ort-fishing/printable-versions"
            ),
        ),
        "species_ranges": SourceSpec(
            status="implemented",
            runner=f"{_INGEST}:run_species_ranges",
            area_scoped=False,
            label="QC species ranges",
            ttl_days=365,
            ttl_constant=f"{_PKG}.species_ranges:_CACHE_TTL_SECONDS",
            url="https://www.donneesquebec.ca/recherche/dataset/aires-de-repartition-faune",
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
            "St Lawrence River Montreal",
            45.5,
            -73.56,
            60.0,
            sources=("global", "species_ranges", "regulations", "water_quality"),
        ),
        CronArea(
            "Riviere Saint-Maurice",
            46.35,
            -72.55,
            40.0,
            sources=("global", "species_ranges", "regulations", "water_quality"),
        ),
        CronArea(
            "Saguenay River",
            48.42,
            -71.07,
            40.0,
            sources=("global", "species_ranges", "regulations", "water_quality"),
        ),
        CronArea(
            "Riviere Gatineau",
            45.48,
            -75.7,
            40.0,
            sources=("global", "species_ranges", "regulations", "water_quality"),
        ),
    ),
    regulations_adapter=f"{_PKG}.regulations",
    status_authorities=(
        "COSEWIC",
        "SARA",
        "Quebec Act respecting threatened or vulnerable species",
    ),
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any QC lat/lng. "
        "HYDRO: Réseau hydrographique du Québec (RHN) — no queryable WFS found as of 2026; "
        "OSM covers QC rivers adequately. WATER QUALITY: MELCCFP Réseau-rivières via Données "
        "Québec (pH, temperature, conductivity; no DO or turbidity) — see "
        "ca_qc/water_quality.py. REGULATIONS: per-zone 'Fishing periods, limits and "
        "exceptions' PDFs from the MELCCFP Regpec service, linked from the "
        "printable-versions page. The idZone query param does NOT match the printed zone "
        "number (Zone 14→idZone 15, Zone 19 north→idZone 3063, etc.), so the adapter "
        "re-derives every (idZone, label) pair from the live page each run and logs loudly "
        "if the page layout changes — never hardcoded. One chunk per zone (34 total as of "
        "the 2026-2027 season incl. split sub-zones 13e/w, 19n/sA/sB, 22n/s, 23n/s) with "
        "sequential zone ids and the published label in zone_name; regulation_year parsed "
        "from the PDF's own season header so it auto-advances. Province-wide general rules "
        "stay on an HTML page and are not ingested. SPECIES_RANGES: MELCCFP GeoJSON — "
        "verified 118 freshwater fish species extract correctly (an earlier version returned "
        "0 records — wrong property names). No COSEWIC/SARA status field exists in this file "
        "(contrary to an earlier note here); coordinates require reprojection from "
        "EPSG:32198 (pyproj, added as a dependency) — not the fiona/GDAL-requiring .gdb. "
        "Ingest uses upsert_species_ranges_merged so QC species shared with CA-ON "
        "(Largemouth Bass, Walleye, ...) get their jurisdictions_present unioned rather than "
        "overwritten — see species_ranges.py."
    ),
)
