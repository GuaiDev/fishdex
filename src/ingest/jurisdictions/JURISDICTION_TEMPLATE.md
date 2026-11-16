# Adding a New Jurisdiction

This document is a checklist for adding a new jurisdiction (province, state,
or country) to the FishDex ingest pipeline. After reading it, hand it to Claude
Code with the jurisdiction name and any known data source URLs; it will do the
rest.

---

## What you get for free

These global sources work for any lat/lng without any jurisdiction-specific code:

| Source | What it provides | Adapter |
|--------|-----------------|---------|
| iNaturalist | Recent fish observations (last 90 days) | `src/ingest/global/inaturalist.py` |
| GBIF | Museum specimens + historical surveys | `src/ingest/global/gbif.py` |
| WSC stream gauges | Live stream height + discharge | `src/ingest/global/wsc.py` |
| OpenStreetMap | Water features + road/parking access points | `src/ingest/global/osm.py` |
| eBird | Piscivore bird observations (proxy for fish) | `src/ingest/global/ebird.py` |
| Open-Meteo weather | Live weather + barometric pressure | `src/ingest/global/weather.py` |
| CABIN benthic | Federal macroinvertebrate data (all Canada) | `src/ingest/jurisdictions/ca_on/benthic.py` |

For CLI ingestion and regional refresh commands, see
[README.md](../../../README.md#running-locally-and-hosting). No API ingest
endpoint runs adapters.

---

## What needs to be built per jurisdiction

Create a new folder `src/ingest/jurisdictions/<code>/` where `<code>` is the
ISO 3166-2 code in lowercase (e.g. `ca_bc`, `us_mi`, `ca_ab`).

### Required files

| File | What it does |
|------|-------------|
| `__init__.py` | Empty (just makes it a Python package) |
| `hydro_network.py` | Stream segments from the jurisdiction's official stream network |
| `water_quality.py` | Water quality readings (DO, pH, temp, conductivity) |

### Optional files (build when data exists)

| File | What it does |
|------|-------------|
| `fish_observations.py` | Official fish observation / inventory records |
| `stocking.py` | Hatchery stocking records |
| `regulations.py` | Fishing regulation zones |
| `species_ranges.py` | Native range maps |
| `geology.py` | Surficial geology / substrate |

---

## Standard table schemas

Every adapter must write to one of these tables. **Add the `jurisdiction` column
value for your jurisdiction code on every row.**

### `stream_segments`

| Column | Type | Notes |
|--------|------|-------|
| `ogf_id` | INTEGER PK | Source system's native integer ID (OHN: OGF_ID, FWA: LINEAR_FEATURE_ID) |
| `watercourse_type` | TEXT | "Stream", "River", "Canal", etc. |
| `name` | TEXT | Official name (nullable) |
| `flow_verified` | INTEGER | 0/1; use 0 if source has no flow-direction data |
| `permanency` | TEXT | "Permanent" or seasonal description |
| `flow_classification` | TEXT | Nullable; source-specific classification |
| `stream_order` | INTEGER | Strahler order (nullable if not in source) |
| `length_m` | REAL | Segment length in metres |
| `geom_wkt` | TEXT | WKT LineString or MultiLineString (lon lat coords) |
| `start_node` | TEXT | "lon,lat" rounded to 5 dp |
| `end_node` | TEXT | "lon,lat" rounded to 5 dp |
| `jurisdiction` | TEXT | **e.g. "CA-BC"** |
| `segment_source` | TEXT | Source system name: "OHN", "FWA", "NHD", etc. |
| `ingested_at` | TEXT | ISO datetime |

### `observations`

Shared with iNaturalist (source='iNaturalist') and FISS (source='FISS').

| Column | Type | Notes |
|--------|------|-------|
| `observation_id` | INTEGER PK | Source system's native integer ID |
| `species` | TEXT | Scientific name preferred; common name acceptable |
| `common_name` | TEXT | Nullable |
| `taxon_id` | INTEGER | Source taxonomy ID (nullable) |
| `lat` | REAL | |
| `lng` | REAL | |
| `observed_on` | TEXT | ISO date string; use "1900-01-01" for unknown dates |
| `quality_grade` | TEXT | "research", "needs_id", "survey_data", etc. |
| `photo_url` | TEXT | Nullable |
| `observer` | TEXT | Nullable |
| `place_guess` | TEXT | Waterbody or location description (nullable) |
| `jurisdiction` | TEXT | **e.g. "CA-BC"** |
| `ingested_at` | TEXT | ISO datetime |
| `geoprivacy` | TEXT | "open", "obscured", "private" |
| `is_obscured` | INTEGER | 0/1 |
| `obscuration_radius_km` | REAL | Nullable |
| `source` | TEXT | **e.g. "FISS", "iNaturalist"** |

### `water_quality_readings`

| Column | Type | Notes |
|--------|------|-------|
| `record_id` | TEXT PK | Source-unique record ID |
| `station_id` | TEXT | Monitoring station ID |
| `station_name` | TEXT | Nullable |
| `lat` | REAL | |
| `lng` | REAL | |
| `jurisdiction` | TEXT | **e.g. "CA-BC"** |
| `sampled_at` | TEXT | ISO date string |
| `do_mgl` | REAL | Dissolved oxygen mg/L (nullable) |
| `ph` | REAL | (nullable) |
| `temp_c` | REAL | (nullable) |
| `conductivity_us_cm` | REAL | (nullable) |
| `turbidity_fnu` | REAL | (nullable) |

---

## Checklist

```
[ ] Create src/ingest/jurisdictions/<code>/__init__.py
[ ] Implement hydro_network.py — fetch_watercourses(lat, lon, radius_km) → list[StreamSegment]
[ ] Implement water_quality.py — fetch_water_quality_readings(lat, lng, radius_km) → list[WaterQualityReading]
[ ] Implement fish_observations.py if an official fish obs layer exists
[ ] Create src/ingest/jurisdictions/<code>/plugin.py exporting PLUGIN = JurisdictionPlugin(...)
    (code, name, bbox, sources, cron_areas, status_authorities, ...) — see
    src/jurisdictions/plugin.py and any existing folder (ca_mb/plugin.py is a small one).
    This is the only registration: the registry, coordinate tagging, the prompt,
    `weekly-ingest` and `ingest-source` all find the folder by scanning.
[ ] Create src/ingest/jurisdictions/<code>/ingest.py with one run_<source>(ctx: AreaContext)
    per runnable source, and name each in the plugin's `sources`
[ ] Confirm all adapters write jurisdiction="<CODE>" and correct segment_source / source
[ ] Run make lint; fix any issues
[ ] Test with a small radius (radius_km=10) against a known location in the jurisdiction
```

---

## API endpoints by jurisdiction

### Canada — DataBC WFS pattern

All BC layers follow this pattern:
```
https://openmaps.gov.bc.ca/geo/pub/<LAYER_NAME>/ows
  ?service=WFS&version=2.0.0&request=GetFeature
  &typeName=pub:<LAYER_NAME>
  &outputFormat=application/json&srsName=EPSG:4326
  &bbox=<min_lon>,<min_lat>,<max_lon>,<max_lat>
  &count=<page_size>&startIndex=<offset>
```
BBOX is **lon/lat** (x first) for EPSG:4326 on DataBC servers.

### Canada — Alberta (CA-AB)

- Stream network: AltaLIS Hydrography (restricted) or the National Hydrographic Network (NHN)
- Water quality: Alberta Environment monitoring network (AEIMN) — check data.alberta.ca
- Fish observations: Alberta FWMIS (Fish and Wildlife Management Information System) — limited public access

### Canada — Quebec (CA-QC)

- Stream network: Réseau hydrographique du Québec (RHN) via données.gouv.qc.ca
- Water quality: MELCC water quality network — https://www.environnement.gouv.qc.ca
- Fish observations: Faune Québec — limited public access

### USA — NHD pattern

For any US state, the National Hydrography Dataset (NHD) provides stream networks:
```
https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer
```
Query by bounding box using ArcGIS REST (same pattern as OHN adapter). Use
`NHDFlowline` layer for stream segments.

US water quality: EPA WQX (Water Quality Exchange):
```
https://www.waterqualitydata.us/data/Result/search?bBox=<minLon>,<minLat>,<maxLon>,<maxLat>&mimeType=csv
```

US fish observations: US Fish & Wildlife Service FWS or state agency CPUE data.
Michigan example: MDNR Fish Stocking — https://www2.dnr.state.mi.us/fishstock/

---

## Notes on jurisdiction collisions

`stream_segments.ogf_id` is the PK (integer). Since each jurisdiction's source
system assigns its own sequential integers, IDs can theoretically collide across
jurisdictions. In practice, OHN IDs and FWA LINEAR_FEATURE_IDs occupy very
different ranges. If collision becomes a problem for a future jurisdiction, add
a `UNIQUE(ogf_id, jurisdiction)` constraint and change the PK.

For `observations.observation_id`: iNaturalist IDs are in the hundreds of
millions; official survey systems (FISS, MDNR, etc.) use much smaller integers.
Collision is extremely unlikely in practice. The `source` column is the reliable
deduplication key.
