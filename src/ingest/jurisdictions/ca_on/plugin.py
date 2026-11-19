"""Ontario (CA-ON) — the first fully-modeled jurisdiction.

Ontario's datasets are run by `fishbot ingest` (the home-area bundle), not by
the generic per-area runner, so its sources declare no `runner`. Its weekly
areas run the global bundle only.
"""

from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

_OGL_ON = "Open Government Licence – Ontario"
_BY = "fishbot ingest"
_ca_on = "src.ingest.jurisdictions.ca_on"

_REGULATORY_CONTEXT = (
    "## Ontario (CA-ON)\n"
    "\n"
    "Ontario fishing is regulated by the Ontario Ministry of Natural Resources and "
    "Forestry (MNRF).\n"
    "\n"
    "- Anglers aged 18-64 need an Outdoors Card plus a valid fishing licence.\n"
    "- Regulations are zone-based: Ontario is split into 20 Fisheries Management "
    "Zones (FMZs). Seasons, limits, and slot sizes vary by zone and by water body.\n"
    "- The annual Recreational Fishing Regulations Summary published by MNRF is the "
    "authoritative reference. Specific water bodies can be checked on Fish ON-Line.\n"
    "- Border waters with the US (Lake Erie, Huron, Ontario, St. Lawrence River, "
    "Lake of the Woods, Rainy Lake) have binational considerations — possession may "
    "depend on which side of the line a fish was caught on.\n"
    "- Indigenous/First Nations waters and reserves are governed separately. If a "
    "question concerns those waters, flag it and direct the user to the relevant "
    "First Nation's authority.\n"
    "\n"
    "Use the get_regulations tool to look up actual zone text from the MNRF "
    "Recreational Fishing Regulations Summary (parsed from the official PDF). Always "
    "remind the user to verify current-year specifics with MNRF before relying on "
    "any specific limit, season, or slot size."
)

PLUGIN = JurisdictionPlugin(
    code="CA-ON",
    name="Ontario",
    country="CA",
    has_detailed_data=True,
    regulatory_context=_REGULATORY_CONTEXT,
    bbox=BBox(41.6, 56.9, -95.2, -74.3, priority=10),
    sources={
        "hydro_network": SourceSpec(
            status="implemented",
            ttl_days=30,
            ttl_constant=f"{_ca_on}.hydro_network:_CACHE_TTL_SECONDS",
            licence=_OGL_ON,
            url=(
                "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/"
                "LIO_OPEN_DATA/LIO_Open01/MapServer"
            ),
            note=f"Ontario Hydro Network via LIO ArcGIS MapServer. Run by `{_BY}`.",
        ),
        "fish_observations": SourceSpec(
            status="planned",
            note="Covered by global iNaturalist + GBIF; MNRF survey data is not public.",
        ),
        "water_quality": SourceSpec(
            status="implemented",
            ttl_days=30,
            ttl_constant=f"{_ca_on}.water_quality:_STATIONS_TTL",
            licence=_OGL_ON,
            url=(
                "https://data.ontario.ca/api/3/action/package_show"
                "?id=provincial-stream-water-quality-monitoring-network"
            ),
            note=f"PWQMN. Run by `{_BY}`.",
        ),
        "stocking": SourceSpec(
            status="implemented",
            ttl_days=30,
            ttl_constant=f"{_ca_on}.stocking:_TTL_SECONDS",
            licence=_OGL_ON,
            url="https://geohub.lio.gov.on.ca/datasets/c725d683af734e6da7850fe0f0b73eb3_0.csv",
            note=f"MNRF stocking history. Run by `{_BY}`.",
        ),
        "regulations": SourceSpec(
            status="implemented",
            area_scoped=False,
            ttl_days=365,
            ttl_constant=f"{_ca_on}.regulations:_TTL_SECONDS",
            licence=_OGL_ON,
            note=f"MNRF annual regulations PDF. Run by `{_BY}`.",
        ),
        "species_ranges": SourceSpec(
            status="implemented",
            area_scoped=False,
            note=f"Hardcoded range maps. Run by `{_BY}`.",
        ),
        "benthic": SourceSpec(
            status="implemented",
            ttl_days=30,
            ttl_constant=f"{_ca_on}.benthic:_TTL",
            note=f"CABIN (federal; filtered to ON). Run by `{_BY}`.",
        ),
        "geology": SourceSpec(
            status="implemented",
            ttl_days=365,
            ttl_constant=f"{_ca_on}.geology:_TTL",
            note=f"MRD 128 surficial geology. Run by `{_BY}`.",
        ),
        "reddit": SourceSpec(
            status="disabled",
            note="Reddit scraping is off for beta: no corpus is shipped and the "
            "unauthenticated JSON endpoint is against Reddit's terms at scale.",
        ),
    },
    cron_areas=(
        CronArea("Grand River Dunnville", 42.917, -79.774, 50),
        CronArea("Credit River Mississauga", 43.55, -79.65, 30),
        CronArea("Bronte Creek Oakville", 43.45, -79.72, 25),
        CronArea("Thames River London", 42.984, -81.244, 30),
    ),
    hydro_adapter=f"{_ca_on}.hydro_network",
    regulations_adapter=f"{_ca_on}.regulations",
    status_authorities=("COSEWIC", "SARA", "Ontario Endangered Species Act"),
    curation={
        "exclusions": "data/curation/first_nations_waters_ca_on.toml",
        "stretches": "data/curation/stretches_ca_on.toml",
    },
)
