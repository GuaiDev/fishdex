"""Alberta waterbody species presence — FWMIS hydro polygons SPECIES_PRES.

Source: FWMIS Fisheries & Wildlife Management Information System via
Geospatial Alberta ArcGIS FeatureServer.
  Service:  fwmis_hydrography
  Layer 1:  fwmis_hydro_polygons (waterbody polygons)

Endpoint:
  https://geospatial.alberta.ca/titan/rest/services/fisheries/fwmis_hydrography/FeatureServer/1/query

The layer maps every surveyed waterbody to a comma-separated list of FWMIS
species codes in the SPECIES_PRES field, refreshed quarterly. The sentinel
value 'NO FISH SAMPLED TO DATE' means the waterbody has no survey record at all
— it is not evidence of absence and must never be stored as one.

Species codes are decoded through the official FWMIS fisheries loadform code
list, released under the Open Government Licence - Alberta and mirrored in the
fishbc R package's `ab` dataset (https://www.alberta.ca/fisheries-loadforms).
A code that is not in the table is kept verbatim as the species label — never
guessed. Family-level codes (FAMCATO "Sucker Family", FAMCYPR "Minnow Family")
and hybrids (BLBK, CRTR, NFDC, SPLA, TLWH) are genuine source values and are
preserved exactly as coded.

Records live in the shared `observations` table with source='FWMIS' and
jurisdiction='CA-AB', one row per (waterbody, species). The observation point
is the waterbody polygon's centroid returned by the service under
outSR=4326 (verified live — WGS84 lon/lat, no reprojection needed).
observed_on uses the FISS sentinel `_UNKNOWN_DATE` (1900-01-01) because the
source carries no survey date; per `query_observations`, non-iNaturalist rows
with that sentinel are always included regardless of recency window.

A given WB_ID can map to several polygons (e.g. a lake and its bays sharing a
waterbody id). All polygons for one WB_ID are aggregated: the species union is
reported once, anchored at the centroid of the polygon with the greatest
Shape__Area (the main waterbody part), so the same fish is never double-counted.

observation_id is namespaced to a reserved block:
    8_000_000_000 + WB_ID * 1000 + species_index
This avoids the two native id spaces already in the shared table — FISS's 6–7
digit provincial ids and iNaturalist's (hundreds of millions and growing);
WB_IDs run only into the low hundred-thousands. Same collision reasoning the
FISS adapter documents for its own ids, with a wider margin.

Cached: reuses the FWMIS cache under data/cache/fwmis (30-day TTL) shared with
the hydro-arc adapter — the pagination, tiling and cache helpers live in
`hydro_network` and are reused here rather than duplicated.
"""

import logging
from datetime import date

from src.ingest.jurisdictions.ca_ab.hydro_network import (  # noqa: PLC2701
    _PAGE_SIZE,
    _bbox,
    _fetch_tile,
    _grid_tiles,
)
from src.models.observation import Observation

_SERVICE_BASE = (
    "https://geospatial.alberta.ca/titan/rest/services/fisheries/fwmis_hydrography/FeatureServer/1"
)
# Sentinel duplicate of the one the FISS adapter uses (date(1900,1,1) validates)
_UNKNOWN_DATE = date(1900, 1, 1)
# No-survey sentinel used across the SPECIES_PRES field
_NO_SURVEY_SENTINEL = "NO FISH SAMPLED TO DATE"
# Reserved id block, far above FISS (<=7 digits) and iNaturalist (>=9 digits)
_ID_BASE = 8_000_000_000
# Slots per waterbody for species positions; real lists are <= ~90 codes
_SPP_SLOTS = 1000

# Official FWMIS fisheries loadform codes (Open Government Licence - Alberta),
# as mirrored by the fishbc R package `ab` dataset. Source link in module doc.
_SPECIES_CODES: dict[str, str] = {
    "AFJW": "African Jewelfish",
    "AGMN": "Arctic Grayling (Belly Popln)",
    "ARCH": "Arctic Char",
    "ARGR": "Arctic Grayling",
    "ARLM": "Arctic Lamprey",
    "ARTR": "Athabasca Rainbow Trout",
    "BKTR": "Brook Trout",
    "BLBK": "Bull Trout X Brook Trout Hybrid",
    "BLTR": "Bull Trout",
    "BNTR": "Brown Trout",
    "BRMN": "Brassy Minnow",
    "BRST": "Brook Stickleback",
    "BURB": "Burbot",
    "CCHL": "Cichlid",
    "CHSL": "Coho Salmon",
    "CISC": "Tullibee (Cisco)",
    "CRCA": "Crucian Carp",
    "CRTR": "Cutthroat Trout X Rainbow Trout",
    "CTTR": "Cutthroat Trout",
    "DLVR": "Dolly Varden",
    "DPSC": "Deepwater Sculpin",
    "EMSH": "Emerald Shiner",
    "FAMCATO": "Sucker Family",
    "FAMCYPR": "Minnow Family",
    "FLCH": "Flathead Chub",
    "FNDC": "Finescale Dace",
    "FTMN": "Fathead Minnow",
    "GLTR": "Golden Trout",
    "GOFS": "Goldfish",
    "GOLD": "Goldeye",
    "GSCA": "Grass Carp",
    "IWDR": "Iowa Darter",
    "KOIF": "Koi",
    "KOKA": "Kokanee",
    "LGPR": "Logperch",
    "LKCH": "Lake Chub",
    "LKST": "Lake Sturgeon",
    "LKTR": "Lake Trout",
    "LKWH": "Lake Whitefish",
    "LNDC": "Longnose Dace",
    "LNSC": "Longnose Sucker",
    "LRSC": "Largescale Sucker",
    "MNSC": "Mountain Sucker",
    "MNWH": "Mountain Whitefish",
    "MOON": "Mooneye",
    "NFDC": "Northern Redbelly Dace X Finescale Dace",
    "NNST": "Ninespine Stickleback",
    "NOCY": "Northern Crayfish",
    "NRDC": "Northern Redbelly Dace",
    "NRPK": "Northern Pike",
    "NRSQ": "Northern Pikeminnow",
    "PGWH": "Pygmy Whitefish",
    "PMCH": "Peamouth Chub",
    "PRCR": "Prussian Carp",
    "PRDC": "Pearl Dace",
    "PRSC": "Prickly Sculpin",
    "QUIL": "Quillback",
    "RDSH": "Redside Shiner",
    "RMSC": "Rocky Mountain Sculpin",
    "RNTR": "Rainbow Trout",
    "RNWH": "Round Whitefish",
    "RRMN": "Rosy Red Minnow",
    "RVSH": "River Shiner",
    "SAUG": "Sauger",
    "SHCS": "Shortjaw Cisco",
    "SHRD": "Shorthead Redhorse",
    "SLML": "Sailfin Molly",
    "SLMP": "Sea Lamprey",
    "SLRD": "Silver Redhorse",
    "SLSC": "Slimy Sculpin",
    "SMBS": "Smallmouth Bass",
    "SPLA": "Splake",
    "SPSC": "Spoonhead Sculpin",
    "SPSH": "Spottail Shiner",
    "STON": "Stonecat",
    "TGTR": "Tiger Trout",
    "THST": "Threespine Stickleback",
    "TLWH": "Tullibee (Cisco) X Lake Whitefish",
    "TRPR": "Trout-Perch",
    "WALL": "Walleye",
    "WEMO": "Western Mosquitofish",
    "WHSC": "White Sucker",
    "WSCT": "Westslope Cutthroat Trout",
    "WSMN": "Western Silvery Minnow",
    "YLPR": "Yellow Perch",
}

logger = logging.getLogger(__name__)


def fetch_waterbody_presence(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
) -> list[Observation]:
    """Fetch FWMIS waterbody species presence within radius_km of lat/lng.

    Returns one Observation per (waterbody, species), anchored at the main
    polygon centroid. Cached 30 days (shared FWMIS cache). Returns [] when the
    area has no surveyed waterbodies.
    """
    min_lon, min_lat, max_lon, max_lat = _bbox(lat, lng, radius_km)
    base_params = {
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": ("OBJECTID,WB_ID,OFFICIAL_NM,COMMON_NM,Feature_Type,SPECIES_PRES,Shape__Area"),
        "returnGeometry": "false",
        "returnCentroid": "true",
        "resultRecordCount": _PAGE_SIZE,
        "f": "json",
    }
    url = f"{_SERVICE_BASE}/query"

    features: dict[int, dict] = {}
    tiles = list(_grid_tiles(min_lon, min_lat, max_lon, max_lat))
    logger.info("FWMIS presence: fetching %d sub-tiles for %.0fkm radius", len(tiles), radius_km)
    for i, (t_min_lon, t_min_lat, t_max_lon, t_max_lat) in enumerate(tiles, 1):
        for feat in _fetch_tile(url, base_params, t_min_lon, t_min_lat, t_max_lon, t_max_lat):
            obj_id = feat.get("attributes", {}).get("OBJECTID")
            if obj_id is not None and obj_id not in features:
                features[obj_id] = feat
        if i % 50 == 0:
            logger.info(
                "FWMIS presence: processed %d/%d tiles (%d features so far)",
                i,
                len(tiles),
                len(features),
            )

    observations, unmapped, no_survey = _parse_presence(features.values())
    if unmapped:
        count = len(unmapped)
        log = logger.warning if count >= max(1, len(observations) // 100) else logger.info
        log(
            "FWMIS presence: %d species code(s) not in the loadform table (kept verbatim): %s",
            count,
            ", ".join(sorted(unmapped)),
        )
    if no_survey:
        # A "no fish sampled" waterbody is not a fish-less one — say how many
        # were skipped so a silent 0-feature ingest stays impossible to confuse
        # with a genuinely unsampled area.
        logger.info("FWMIS presence: %d waterbody(ies) with no survey record skipped", no_survey)

    logger.info("FWMIS presence fetch complete: %d species/waterbody records", len(observations))
    return observations


# ── parser ────────────────────────────────────────────────────────────────────


def _parse_presence(features) -> tuple[list[Observation], set[str], int]:
    """Turn raw polygon features into observations.

    Returns (observations, unmapped_codes, no_survey_count). Aggregates one
    WB_ID across polygons: species union, anchored at the largest-area polygon.
    """
    by_wb: dict[int, dict] = {}

    for feat in features:
        attrs = feat.get("attributes", {})
        wb_id = attrs.get("WB_ID")
        if wb_id is None:
            continue
        area = float(attrs.get("Shape__Area") or 0.0)
        club = by_wb.get(wb_id)
        if club is None:
            club = {
                "name": _waterbody_name(attrs),
                "largest_area": area,
                "centroid": _centroid_ll(feat),
                "codes": set(),
                "features": 0,
            }
            by_wb[wb_id] = club
        club["features"] += 1
        if area > club["largest_area"]:
            club["largest_area"] = area
            club["centroid"] = _centroid_ll(feat)
            club["name"] = _waterbody_name(attrs)
        club["codes"].update(_split_codes(attrs.get("SPECIES_PRES")))

    observations: list[Observation] = []
    unmapped: set[str] = set()
    no_survey = 0

    for wb_id, club in by_wb.items():
        if not club["codes"]:
            no_survey += 1
            continue
        if club["centroid"] is None:
            continue
        lat, lng = club["centroid"]
        sorted_codes = sorted(club["codes"])
        for idx, code in enumerate(sorted_codes):
            if code in _SPECIES_CODES:
                species = _SPECIES_CODES[code]
            else:
                species = code
                unmapped.add(code)
            observations.append(
                Observation(
                    observation_id=_ID_BASE + wb_id * _SPP_SLOTS + idx,
                    species=species,
                    common_name=species,
                    taxon_id=None,
                    lat=lat,
                    lng=lng,
                    observed_on=_UNKNOWN_DATE,
                    quality_grade="survey_data",  # FISS-style survey attribution
                    photo_url=None,
                    observer=None,
                    place_guess=club["name"],
                    jurisdiction="CA-AB",
                    geoprivacy="open",
                    is_obscured=False,
                    obscuration_radius_km=None,
                    source="FWMIS",
                )
            )

    return observations, unmapped, no_survey


def _waterbody_name(attrs: dict) -> str | None:
    name = attrs.get("COMMON_NM") or attrs.get("OFFICIAL_NM") or None
    if name in (None, "UNNAMED"):
        return None
    return name


def _centroid_ll(feat: dict) -> tuple[float, float] | None:
    """Extract (lat, lng) from the ArcGIS returnCentroid point (WGS84)."""
    cen = feat.get("centroid") or {}
    if "x" not in cen or "y" not in cen:
        return None
    return float(cen["y"]), float(cen["x"])


def _split_codes(raw) -> set[str]:
    """Split a SPECIES_PRES value into trimmed, uppercased codes."""
    if not raw or not isinstance(raw, str):
        return set()
    cleaned = raw.strip()
    if not cleaned or cleaned.upper() == _NO_SURVEY_SENTINEL:
        return set()
    return {c.strip().upper() for c in cleaned.split(",") if c.strip()}
