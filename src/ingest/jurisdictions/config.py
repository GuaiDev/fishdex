"""Jurisdiction config registry for the FishDex ingest pipeline.

Each jurisdiction registers a JurisdictionConfig that documents:
  - Which global sources work automatically (iNat, GBIF, WSC, OSM, eBird)
  - Which jurisdiction-specific adapters are implemented
  - Cron areas used by the weekly GitHub Actions ingest
  - API endpoints / dataset URLs for jurisdiction-specific sources

Usage
-----
    from src.ingest.jurisdictions.config import get, all_jurisdictions
    cfg = get("CA-BC")
    cfg.cron_areas      # list of CronArea
    cfg.data_sources    # dict[str, bool | None]

Adding a new jurisdiction
-------------------------
See src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CronArea:
    label: str
    lat: float
    lng: float
    radius_km: float


@dataclass
class JurisdictionConfig:
    jurisdiction_code: str   # ISO 3166-2: "CA-ON", "CA-BC", "US-MI"
    display_name: str
    cron_areas: list[CronArea]
    # Keys: "hydro_network", "fish_observations", "water_quality", "stocking",
    #       "regulations", "species_ranges", "benthic", "geology"
    # Values: True = implemented, False = planned but not yet built, None = not applicable
    data_sources: dict[str, bool | None] = field(default_factory=dict)
    # Named API endpoints / dataset URLs for jurisdiction-specific sources
    api_endpoints: dict[str, str] = field(default_factory=dict)
    notes: str = ""


# ── registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, JurisdictionConfig] = {}


def register(config: JurisdictionConfig) -> None:
    _REGISTRY[config.jurisdiction_code] = config


def get(jurisdiction_code: str) -> JurisdictionConfig | None:
    return _REGISTRY.get(jurisdiction_code)


def all_jurisdictions() -> list[JurisdictionConfig]:
    return list(_REGISTRY.values())


# ── built-in registrations ────────────────────────────────────────────────────

register(JurisdictionConfig(
    jurisdiction_code="CA-ON",
    display_name="Ontario",
    cron_areas=[
        CronArea("Grand River Dunnville",     42.917, -79.774, 50),
        CronArea("Credit River Mississauga",  43.55,  -79.65,  30),
        CronArea("Bronte Creek Oakville",     43.45,  -79.72,  25),
        CronArea("Thames River London",       42.984, -81.244, 30),
    ],
    data_sources={
        "hydro_network":     True,   # OHN via LIO ArcGIS MapServer
        "fish_observations": False,  # covered by global iNat + GBIF
        "water_quality":     True,   # PWQMN
        "stocking":          True,   # MNRF
        "regulations":       True,   # MNRF annual PDF
        "species_ranges":    True,   # hardcoded range maps
        "benthic":           True,   # CABIN (federal; filtered to ON)
        "geology":           True,   # MRD 128 surficial geology
    },
    api_endpoints={
        "hydro_network":  (
            "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/"
            "LIO_OPEN_DATA/LIO_Open01/MapServer"
        ),
        "water_quality":  (
            "https://data.ontario.ca/api/3/action/package_show"
            "?id=provincial-stream-water-quality-monitoring-network"
        ),
        "stocking": (
            "https://geohub.lio.gov.on.ca/datasets/"
            "c725d683af734e6da7850fe0f0b73eb3_0.csv"
        ),
    },
))

register(JurisdictionConfig(
    jurisdiction_code="CA-BC",
    display_name="British Columbia",
    cron_areas=[
        CronArea("Fraser River Lower Mainland", 49.12,  -122.05, 60),
        CronArea("Thompson River Kamloops",      50.67,  -120.33, 50),
        CronArea("Okanagan Vernon",              50.27,  -119.27, 40),
        CronArea("Skeena River Terrace",         54.51,  -128.60, 50),
    ],
    data_sources={
        "hydro_network":     True,   # FWA via DataBC WFS
        "fish_observations": True,   # FISS via DataBC WFS
        "water_quality":     True,   # BC EMS stations via DataBC WFS (results stubbed)
        "stocking":          True,   # FISS stocking extraction (ACTIVITY_CODE filter)
        "regulations":       True,   # BC 2025-2027 synopsis PDF
        "species_ranges":    False,  # not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
        "geology":           False,  # BC surficial geology adapter not yet built
        "salmon_escapement": True,   # NuSEDS DFO (openpyxl required)
    },
    api_endpoints={
        "hydro_network": (
            "https://openmaps.gov.bc.ca/geo/pub/"
            "WHSE_BASEMAPPING.FWA_STREAM_NETWORKS_SP/ows"
        ),
        "fish_observations": (
            "https://openmaps.gov.bc.ca/geo/pub/"
            "WHSE_FISH.FISS_FISH_OBSRVTN_PNT_SP/ows"
        ),
        "water_quality_stations": (
            "https://openmaps.gov.bc.ca/geo/pub/"
            "WHSE_ENVIRONMENTAL_MONITORING.EMS_MONITORING_LOCN_TYPES_SVW/ows"
        ),
        "salmon_escapement": (
            "https://open.canada.ca/data/en/dataset/"
            "c48669a3-045b-400d-b730-48aafe8c5ee6"
        ),
        "regulations": (
            "https://www2.gov.bc.ca/assets/gov/sports-recreation-arts-and-culture/"
            "outdoor-recreation/fishing-and-hunting/freshwater-fishing/fishing_synopsis.pdf"
        ),
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any BC lat/lng. "
        "STOCKING: derived from FISS via ACTIVITY_CODE filter — no quantity data available. "
        "SALMON_ESCAPEMENT: NuSEDS 'All Areas NuSEDS' XLSX (421k records verified 2026-06), "
        "requires openpyxl; URL resolved dynamically via CKAN package_show since the dated "
        "attachment filename changes every release — see nuseds.py. No stream coordinates "
        "in the source data as of the 2026-06 release. "
        "REGULATIONS: 2025-2027 synopsis now has 9 regions (7 split into 7A/Omineca + "
        "7B/Peace, new 8/Okanagan) — zone stores 71/72 for the split pair; verified all 9 "
        "extract correctly (39k-58k chars each) despite a PDF text-layer artifact that "
        "doubles every character in Region 7A's running header — see regulations.py. "
        "WATER QUALITY: stations WFS works; results still stubbed. Source system "
        "migrated EMS->EnMoDS 2026-03 (old EMS results resource ID was stale/dead) — "
        "see water_quality.py TODO for current EnMoDS resource URLs and sizes."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-AB",
    display_name="Alberta",
    cron_areas=[
        CronArea("Bow River Calgary",                   51.05,  -114.07, 50),
        CronArea("North Saskatchewan River Edmonton",   53.53,  -113.49, 50),
        CronArea("Oldman River Lethbridge",             49.70,  -112.84, 40),
    ],
    data_sources={
        "hydro_network":     False,  # NHN stub — OSM covers this adequately
        "fish_observations": False,  # AB FWMIS not publicly accessible
        "water_quality":     False,  # stub — no public API
        "stocking":          True,   # planned stocking XLSX from Open Alberta (openpyxl required)
        "regulations":       True,   # Watershed Unit PDF, tested against 2026 edition
        "species_ranges":    False,  # not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
        "geology":           False,  # not yet built
    },
    api_endpoints={
        "stocking": (
            "https://open.alberta.ca/dataset/ae7521d6-7629-4b69-ac45-857fc798c10c"
        ),
        "regulations": (
            "https://open.alberta.ca/dataset/dbf392f4-266f-4947-adc0-fa4bdf4e2c9c"
        ),
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any AB lat/lng. "
        "HYDRO: NHN GeoPackage tiles are available via FTP but no WFS exists; "
        "OSM covers AB streams adequately at order 3+. "
        "REGULATIONS: only 3 Fish Management Zones split into 10 Watershed Units "
        "(ES1-4/PP1-2/NB1-4) — verified all 10 extract correctly against the 2026 edition "
        "(7.5k-31k chars each); URL resolved dynamically via CKAN package_show (picks the "
        "highest-year PDF resource) since a new dated file is added every year — see "
        "regulations.py. "
        "STOCKING: verified against 2026 edition, 527 records — contrary to an earlier "
        "assumption the file DOES include lat/lng; species use AB's BKTR/RNTR/BNTR/TGTR/WSCT "
        "codes (mapped to full names); no clean stocking date, only free-text schedule notes "
        "(preserved in stocking_purpose) — see stocking.py. "
        "WATER QUALITY: AEMERA portal is map-based with no public API; "
        "DataStream (ca_national/) covers some AB watersheds. "
        "STOCKING: coordinates not included in Alberta data — waterbody name only."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-QC",
    display_name="Quebec",
    cron_areas=[
        CronArea("St. Lawrence River Montreal",  45.50,  -73.56,  60),
        CronArea("Rivière Saint-Maurice",        46.35,  -72.55,  40),
        CronArea("Saguenay River",               48.42,  -71.07,  40),
        CronArea("Rivière Gatineau",             45.48,  -75.70,  40),
    ],
    data_sources={
        "hydro_network":     False,  # RHN adapter not yet built
        "fish_observations": False,  # Faune Québec not publicly accessible
        "water_quality":     True,   # MELCCFP Réseau-rivières via Données Québec
        "stocking":          False,  # not publicly available
        "regulations":       False,  # stub — PDF adapter not yet implemented
        "species_ranges":    True,   # MELCCFP GeoJSON via données.gouv.qc.ca
        "benthic":           True,   # CABIN (federal; all provinces)
        "geology":           False,  # not yet built
    },
    api_endpoints={
        "species_ranges": (
            "https://www.donneesquebec.ca/recherche/dataset/aires-de-repartition-faune"
        ),
        "regulations": (
            "https://www.quebec.ca/en/tourism-recreation-sport/"
            "sporting-and-outdoor-activities/sport-fishing/printable-versions"
        ),
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) work automatically for any QC lat/lng. "
        "HYDRO: Réseau hydrographique du Québec (RHN) — no queryable WFS found as of 2026; "
        "OSM covers QC rivers adequately. "
        "WATER QUALITY: MELCCFP Réseau-rivières via Données Québec (pH, temperature, "
        "conductivity; no DO or turbidity) — see ca_qc/water_quality.py. "
        "SPECIES_RANGES: MELCCFP GeoJSON — verified 118 freshwater fish species extract "
        "correctly (an earlier version returned 0 records — wrong property names). No "
        "COSEWIC/SARA status field exists in this file (contrary to an earlier note here); "
        "coordinates require reprojection from EPSG:32198 (pyproj, added as a dependency) — "
        "not the fiona/GDAL-requiring .gdb. Ingest uses upsert_species_ranges_merged so QC "
        "species shared with CA-ON (Largemouth Bass, Walleye, ...) get their "
        "jurisdictions_present unioned rather than overwritten — see species_ranges.py."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-MB",
    display_name="Manitoba",
    cron_areas=[
        CronArea("Red River Winnipeg",      49.90,  -97.14,  50),
        CronArea("Lake Winnipeg South",     50.70,  -96.90,  60),
        CronArea("Assiniboine River",       50.07,  -99.95,  40),
    ],
    data_sources={
        "hydro_network":     False,  # no public WFS found
        "fish_observations": False,  # MB Wildlife Atlas not publicly accessible
        "water_quality":     False,  # DataStream covers some MB watersheds (ca_national/)
        "stocking":          False,  # not publicly available
        "regulations":       False,  # not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Manitoba adequately. "
        "DataStream water quality covers Lake Winnipeg basin — use ca_national/ adapter."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-SK",
    display_name="Saskatchewan",
    cron_areas=[
        CronArea("South Saskatchewan River Saskatoon", 52.13, -106.67, 50),
        CronArea("Qu'Appelle River",                   50.44, -103.82, 40),
        CronArea("Churchill River",                    55.75, -108.45, 50),
    ],
    data_sources={
        "hydro_network":     False,  # no public WFS found
        "fish_observations": False,  # SK Fish and Wildlife not publicly accessible
        "water_quality":     False,  # DataStream covers some SK watersheds (ca_national/)
        "stocking":          False,  # not publicly available
        "regulations":       False,  # not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Saskatchewan adequately. "
        "DataStream water quality may cover some SK watersheds — use ca_national/ adapter."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-NS",
    display_name="Nova Scotia",
    cron_areas=[
        CronArea("Annapolis River NS",  44.98,  -65.50,  40),
        CronArea("Margaree River NS",   46.17,  -61.07,  30),
    ],
    data_sources={
        "hydro_network":     False,  # no public WFS found
        "fish_observations": False,  # NS Inland Fisheries not publicly accessible
        "water_quality":     False,  # DataStream covers Atlantic Canada (ca_national/)
        "stocking":          False,  # not publicly available
        "regulations":       False,  # DFO federal + NS supplement; not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
        "tidal":             True,   # CHS tidal API (ca_national/)
    },
    api_endpoints={
        "regulations": "https://novascotia.ca/fish",
        "tidal": "https://api-sine.dfo-mpo.gc.ca/stations",
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover Nova Scotia. "
        "CHS tidal API provides tide predictions for coastal fishing (ca_national/tidal.py). "
        "DataStream covers Atlantic Canada watersheds."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-NB",
    display_name="New Brunswick",
    cron_areas=[
        CronArea("Miramichi River NB",  46.97,  -65.54,  50),
        CronArea("Saint John River NB", 45.97,  -66.64,  50),
    ],
    data_sources={
        "hydro_network":     False,  # no public WFS found
        "fish_observations": False,  # NB Fish and Wildlife not publicly accessible
        "water_quality":     False,  # DataStream covers Atlantic Canada (ca_national/)
        "stocking":          False,  # not publicly available
        "regulations":       False,  # not yet built
        "benthic":           True,   # CABIN (federal; all provinces)
        "tidal":             True,   # CHS tidal API (ca_national/)
    },
    api_endpoints={
        "regulations": "https://www2.gnb.ca/content/dam/gnb/Departments/nr-rn/pdf/en/Fish/Fish.pdf",
        "tidal": "https://api-sine.dfo-mpo.gc.ca/stations",
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover New Brunswick. "
        "Miramichi is one of the world's best Atlantic salmon rivers — iNat + GBIF coverage good. "
        "CHS tidal API for tidal reach fishing (Miramichi tidal, Saint John tidal). "
        "DataStream covers Atlantic Canada watersheds."
    ),
))

register(JurisdictionConfig(
    jurisdiction_code="CA-PE",
    display_name="Prince Edward Island",
    cron_areas=[],  # PEI is small; add cron areas when needed
    data_sources={
        "hydro_network":     False,  # OSM adequate for PEI's small streams
        "fish_observations": False,  # PEI Agriculture and Fisheries not publicly accessible
        "water_quality":     False,  # DataStream covers Atlantic Canada (ca_national/)
        "stocking":          False,  # not publicly available
        "regulations":       False,  # DFO federal regulations apply
        "benthic":           True,   # CABIN (federal; all provinces)
        "tidal":             True,   # CHS tidal API (ca_national/)
    },
    api_endpoints={
        "regulations": "https://www.dfo-mpo.gc.ca/fisheries-peches/regs/index-eng.html",
        "tidal": "https://api-sine.dfo-mpo.gc.ca/stations",
    },
    notes=(
        "Global sources (iNat, GBIF, WSC, OSM, eBird) cover PEI. "
        "PEI has no large freshwater systems — tidal and coastal fishing dominate. "
        "DFO federal regulations apply for most species."
    ),
))
