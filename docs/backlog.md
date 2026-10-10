# FishDex Backlog

Last updated: July 18 2026 (session 7 — Claude Code session: multi-catch logging + admin dashboard catch stats)
Previously: FishBot

## Project rename
FishBot → FishDex. The name reflects the collection mechanic (personal
Pokédex for fishing). "Bot" undersold the product and alienated senior
anglers. FishDex works for beginners and experts, scales beyond Ontario.

## Architecture split
Development now split into two tracks:
- **Frontend:** Lovable-based UI rebuild, map redesign, branding
- **Backend:** Intelligence improvements, data expansion, coaching

---

## Legend
- ✅ Resolved
- 🔨 In progress
- 📋 Planned
- 💡 Future / research

---

# FRONTEND TRACK

## Branding ✅
- Name: FishDex (was FishBot)
- Tagline: "Find fish. Explore water."
- Color palette: #0A0C10 background, #1D9E75 accent, dark minimal
- Aesthetic: Linear/Raycast dark precision, not outdoorsy
- Lovable prompt written and saved at docs/fishdex_lovable_prompt.md

## React UI (current, to be replaced) ✅
Built as prototype, will be replaced by Lovable build:
- Chat, Log Trip, Trips, Map screens
- Multi-user auth gate
- react-leaflet@4.2.1 (v5 incompatible with React 18)
- Deployed at /app on Railway

### Multi-catch logging UI ✅ (July 18 2026)
Log Trip rebuilt as a session-based flow: session notes captured once (EXIF from
first photo / geolocation fallback), repeatable catch entries (photo, species,
count stepper, size with cm/in toggle persisted to localStorage, bait), add/remove
entries. SpeciesConfirmCard and EXIF parsing kept unchanged.

⚠️ web/dist/ (the built bundle Railway actually serves) has NOT been rebuilt since
this change — the live app still shows the old single-photo/single-textarea screen.
See Pending (session 7 handoff) below.

## Lovable UI rebuild 📋
Use docs/fishdex_lovable_prompt.md to build FishDex in Lovable.
Reference images needed: Fishbrain map, FishAI explore, Beli personal map,
Linear dark UI.
Screens: Login, Chat, Log Trip, Map, Trips (renamed "My FishDex")
Key: react-leaflet@4.2.1, dark theme, collection mechanic feel.

## Map redesign — two-level architecture 📋
Full design document at docs/map_redesign_design_doc.md.
Summary:

### Level 1 — Stretches (low zoom)
~50-80 major named fishing stretches across province/country.
Examples: "Dunnville to Port Maitland", "Lower Credit", "Chatham to Windsor Thames"
Rendered as colored polygons/polylines. Tap to zoom into anatomy.

### Level 2 — Anatomy (high zoom, inside a stretch)
Confluences highlighted as nodes.
Dam tailwaters marked distinctively.
Access points shown (parking, trails, road crossings).
Stream network visible as lines.
Tap any feature → bot synthesis of why it's interesting.

### Personal map (Google Maps replacement for fishing)
Pin states: Saved → Fished → Productive
One tap to save from explore mode.
Only transitions to Fished/Productive when trip is logged.
NEVER shared with other users (gatekeep culture is non-negotiable).

### Explore map
Discovers stretches, anatomy within them.
Save action adds pin to personal collection.
Saved spots visible in explore with filter, not on personal map.

### Schema needed
- fishing_stretches table
- stretch_segments table (many-to-one OHN → stretch)
- stretch_anchors table (confluences, dams, access points)
- personal_pins table (user pins with state)

### Build plan (4 sessions)
1. Stretch definition — algorithmic clustering + manual curation
2. Anchor extraction — confluences, dams, access points
3. Personal pin system — schema + API + trip log integration
4. React/Lovable UI — two-level map with pin states

## Social layer (future, NOT on map) 💡
Catches and PBs only, never spot locations.
Friend caught a PB → notification/feed item.
Location opt-in and COARSE only ("Grand River" not coordinates).
Respects fishing gatekeep culture.

---

# BACKEND TRACK

## Pending (session 6 handoff) 📋
- **DataStream API key** — requested (free registration at datastream.org/en/api),
  waiting for approval. `datastream_water_quality.py`'s query construction was fixed
  this session (see Bug fixes — session 6 below) but the live end-to-end path is
  still unverified without a real key.
- **Merge `design/naturalist-photography-led` → `master`** — this session's 12 commits
  are local-only; this sandbox has no git push credentials configured. Needs a manual
  push + PR/merge.

## Pending (session 7 handoff) 📋
- **Frontend dist rebuild** — web/dist/ predates the LogTrip.jsx multi-catch rebuild.
  Run `npm run build` in web/, then commit web/dist/ — until then, real users get the
  old single-photo/single-textarea Log Trip screen regardless of what's in web/src/.
- **Dev-only login bypass still in code** — App.jsx auto-logs in via POST
  /admin/bootstrap when import.meta.env.DEV is true (also falls back to it on a
  stale/invalid token), and /admin/bootstrap itself is permissive whenever
  FISHBOT_API_KEY is unset. Both are marked "TEMPORARY DEV-ONLY — REMOVE BEFORE
  MERGING/DEPLOYING" in comments but were never removed. Must be stripped before
  onboarding real (non-admin) users — anyone running the app without
  FISHBOT_API_KEY set skips the invite-code Login screen entirely.

## Core Intelligence (completed) ✅

### Trip logging ✅
Natural language parser, sessions/stops schema, mobile logging page,
EXIF GPS extraction, geolocation fallback.

### Multi-catch logging + structured catch persistence ✅ (July 18 2026)
Backend now persists structured per-catch data instead of only round-tripping it
through NL text parsing:
- POST /log-trip and /log-trip/photo accept an optional catches_json field (array of
  {species, count, biggest_size_cm|biggest_size, bait}). Matched against NL-parsed
  species by name — a match merges structured count/size/bait onto that row (species
  confidence/confirmation still comes from the NL+vision suggestion flow); an
  unmatched entry becomes its own row. Absent/empty catches_json is a complete no-op,
  so existing callers (CLI logging, no catches_json) are unaffected.
- New personal_bests table (user_id, species, best_size_cm, catch_id), updated at
  catch-insert time whenever a structured size beats the stored best. GET /fishdex
  now reports a real personal best instead of only a legacy free-text guess.
- /log-trip/photo accepts an additive photos[] field for future multi-photo support;
  the UI currently still uploads one photo per session, same as before.

### Auto-enrichment pipeline ✅
session_conditions: Open-Meteo weather + PWQMN water quality per session.
Anomaly detection vs historical baseline.
Retroactive enrichment script for historical sessions.
8 sessions enriched (sessions 4-10 + 27).

### Behavioral insights ✅
Auto-versioning on contradiction, cache invalidation on update.
Insight #37 (evening-only) correctly versioned out by #40 (morning viable).

### Synthesis cache ✅
Location-based cache, invalidates on insight update.
5 spots pre-warmed.
Time-forward and live-conditions queries bypass cache entirely (June 27 2026).
Forecast responses not written to cache.
Jurisdiction isolation fixed (July 17 2026): the coordinate-based cache path was
already safe (rounds to a ~100m grid, plus a haversine proximity fallback — neither
can cross a provincial border). The name-only fallback path — used whenever a
message gives no coordinates — had zero jurisdiction awareness. Canadian hydronyms
repeat constantly across provinces (Mill Creek, Beaver Creek, Bow River, Trout
Lake...); a synthesis computed for an Ontario "Mill Creek" could have been served
back for a BC "Mill Creek" query. Added a jurisdiction column (derived from
coordinates when known) and a compatibility check before trusting a cache hit.

### Message router ✅
Reflex/synthesis/memory routing. ~95% cost reduction.

### Multi-user auth ✅
Invite codes, Bearer tokens, per-user data isolation, rate limiting.
Admin endpoints, CLI commands.

### SDM models ✅
9 species, ecology-only features, AUC 0.51-0.61 honest.
Trip log flywheel wired in.

### Map segments ✅
47,454 OHN segments imported into map_segments table.
Auto-import on Railway startup.
/map/segments and /map/my-stops endpoints.

## Multi-jurisdiction ingest pipeline ✅ (June 28 2026)
Reusable adapter pattern. Ontario complete (Phase 1). BC, AB, QC, federal, and
Maritime province stubs added in two sessions June 28 2026.

### Phase 1 (BC first jurisdiction)
- `src/ingest/jurisdictions/config.py` — central registry (JurisdictionConfig, CronArea dataclasses)
- `src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md` — checklist + schema reference
- `src/ingest/jurisdictions/ca_bc/` — BC adapters:
  - `hydro_network.py` (FWA stream network via DataBC WFS) — ~1,400 segments per 10km radius
  - `fish_observations.py` (FISS fish observations via DataBC WFS) — ~4,200 obs per 10km radius
  - `water_quality.py` (BC EMS stations) — stations discoverable; results stubbed (see below)
- `src/services/bc_ingest.py` — BC service layer
- `/ingest/data-bc` endpoint — X-Api-Key protected, returns 202 (background task)
- Model additions: `stream_order` + `segment_source` on StreamSegment; `source` on Observation
- DB migrations: idempotent column additions, jurisdiction-scoped deletes on ingest

### Critical bug fixed during build
`ingest_hydro_network()` called `delete_where()` with no filter — wiped ALL stream
segments (both ON and BC) on every Ontario ingest. Fixed to scope by jurisdiction.

### DataBC WFS quirks (document for future jurisdictions)
- All BC layers are native BC Albers (EPSG:3005). Bbox must append `,CRS:84` so the
  server reprojects lon/lat input before spatial filtering — without it, 0 results.
- `srsName=EPSG:4326` only reprojects output geometry if `GEOMETRY` is explicitly in
  `propertyName`; omitting it returns null geometry.
- `startIndex` pagination requires `sortBy=<col>` on layers without a natural PK
  (GeoServer: "Cannot do natural order without a primary key").
- `propertyName` with a non-existent field name returns HTTP 400, not 404.

### BC EMS water quality results — stubbed 📋 (TODO corrected ✅ July 17 2026)
Layer `EMS_MONITORING_LOCN_TYPES_SVW` is correct and still live (verified — a HEAD
request 404s but GET works, same WFS quirk seen elsewhere in this repo).
The old resource ID here (`76be8cdb-95b7-4a96-aae4-f3f59455fbcb`) had gone stale —
investigated why: BC retired EMS in favour of EnMoDS (Environmental Monitoring Data
System) on 2026-03-05; EMS stopped receiving new data on 2026-02-26. Current EnMoDS
results are 4 time-tier CSVs served via the COMS object API (no auth needed for GET,
HTTP Range supported), dataset slug "bc-environmental-monitoring-data-system-results".
The "last 2 years" tier alone is confirmed 336,131,287 bytes (~320MB) via
Content-Range. Plan unchanged in spirit: download the current-tier file (now monthly,
not annually — EnMoDS updates more often than EMS did), filter to nearby
MONITORING_LOCATION_IDs, index locally. Still not implemented — TODO only, per
instruction not to build it out yet.

### Phase 2 — national + AB + QC + Maritimes expansion (June 28 2026)

**Federal/national** (`src/ingest/jurisdictions/ca_national/`):
- `dfo_critical_habitat.py` — DFO SARA critical habitat via ArcGIS REST → `critical_habitat` table
- `dfo_sar_range.py` — DFO SAR critical habitat via ESRI REST (`dfo_sara_critical_habitat/MapServer/0`) → `species_ranges` table; no fiona required
- `tidal.py` — CHS IWLS API (no auth required); `wlp-hilo` series; requires `from`/`to` date params → `tidal_readings` table
- `datastream_water_quality.py` — DataStream OData API → `water_quality_readings` table;
  requires `DATASTREAM_API_KEY` env var (free registration at datastream.org/en/api);
  returns 0 with warning if absent. Query construction fixed July 17 2026 — the join
  field, the coordinate field name, and the spatial filter syntax were all wrong and
  would have returned 0 results even with a valid key (see Bug fixes — session 6).
  Verified against DataStream's public API docs and unit-tested against mocked
  responses; live end-to-end run still blocked on the pending API key (see Pending above).

**BC additions** (`src/ingest/jurisdictions/ca_bc/`):
- `nuseds.py` ✅ tested live July 17 2026, 421,001 records — see Bug fixes — session 6
- `regulations.py` ✅ tested live July 17 2026, all 9 regions — see Bug fixes — session 6
- `fish_observations.py` — stocking extraction added: same WFS call, filters by ACTIVITY_CODE → `stocking_records` table;
  NOTE: FISS stocking records have no quantity field

**Alberta** (`src/ingest/jurisdictions/ca_ab/`):
- `stocking.py` ✅ tested live July 17 2026, 527 records — see Bug fixes — session 6.
  NOTE: an earlier version of this note said AB stocking has no coordinates — that
  was wrong; the real file does have lat/lng.
- `hydro_network.py` ✅ implemented 2026-10-09 — FWMIS Simplified Hydro Arcs
  FeatureServer (fwmis_hydrography layer 0), WGS84 in/out, paginated via
  resultOffset / exceededTransferLimit; 5,013 segments for Bow River Calgary 50km
  radius; stream orders 0–9 populated; no barrier layer (barrier_count=0 for CA-AB).
  Bow + NS Edmonton + Oldman areas wired into weekly ingest.
- `regulations.py` ✅ implemented July 17 2026, 10 chunks — see Bug fixes — session 6
- `fish_observations.py` ✅ implemented 2026-10-10 — FWMIS hydro polygons layer 1
  `SPECIES_PRES` (authoritative waterbody species presence, quarterly refreshed),
  stored in the shared `observations` table (source='FWMIS'); codes decoded via the
  official FWMIS loadform list (85 codes, OGL-Alberta), unknown codes kept verbatim;
  'NO FISH SAMPLED TO DATE' means unsampled, never absence. Caveat: presence is per
  waterbody, not per point — a big-lake centroid is whole-waterbody signal.
- `water_quality.py` — stub (AEMERA portal is map-only; open.alberta.ca has no
  surface-water chemistry; federal national dataset has only 13 AB sites, none near
  the AB areas — re-checked 2026-10-09)

**Quebec** (`src/ingest/jurisdictions/ca_qc/`):
- `species_ranges.py` ✅ tested live July 17 2026, 118 species — see Bug fixes —
  session 6. NOTE: an earlier version of this note claimed COSEWIC status was
  included — it isn't; no conservation-status field exists in the real file.
- `regulations.py` — stub
- `water_quality.py` — stub (MELCCFP RSQER is PDF-only; DataStream covers some QC watersheds)

**Province stubs** (MB, SK, NS, NB, PE `__init__.py`):
- Documents which federal/global sources cover each province
- iNat, GBIF, WSC, OSM, eBird work globally — just need targeted cron areas
- CABIN benthic covers all provinces (see below)
- Coastal provinces (NS, NB, PE): CHS tidal adapter applies

**CABIN benthic expanded**: `load_study`/`parse_benthic`/`build_samples` now return
all Canadian provinces via `visit_jurisdictions: dict[str, str]` (visit_id → jurisdiction).
`ingest_benthic_data()` wrapper preserved for backward compatibility.
Test suite updated to match new signatures (35 tests pass).

**New DB tables**: `critical_habitat`, `tidal_readings`, `salmon_escapement`
**New migration**: `regulation_chunks.zone_name TEXT` added via idempotent `ALTER TABLE`

**New service entry points**:
- `src/services/ab_ingest.py` — AB hydro network, fish observations, stocking, regulations, water quality
- `src/services/qc_ingest.py` — QC species ranges, regulations, water quality
- `src/services/national_ingest.py` — DFO critical habitat, DataStream water quality
- `src/services/tidal.py` — agent tool: `get_tidal_conditions_for_agent(lat, lng)`

**New API endpoints** (all X-Api-Key protected, BackgroundTask, return 202):
- `POST /ingest/data-ab` — Alberta-specific adapters
- `POST /ingest/data-qc` — Quebec-specific adapters
- `POST /ingest/data-national` — DFO critical habitat + DataStream water quality
- `POST /ingest/data-tidal` — CHS tidal predictions

**Jurisdiction config** (`config.py`): CA-AB, CA-QC, CA-MB, CA-SK, CA-NS, CA-NB, CA-PE
registered with cron areas and data_sources dicts. CA-BC updated with
stocking=True, regulations=True, salmon_escapement=True.

### Bug fixes — session 3 (June 28 2026)
Bugs found and fixed during first Railway test run of multi-province adapters:
- **openpyxl** added to pyproject.toml (required by NuSEDS and AB stocking XLSX adapters)
- **CHS tidal**: wrong base URL (`api-sine` → `api-iwls`); missing `/api/v1` prefix on endpoints;
  API requires `from`/`to` date params (400 without them) — now sends today 00:00Z to +7 days,
  rounded to midnight so cache key is stable within a day
- **DFO SAR range**: replaced GDB/fiona approach (GDAL unavailable on Railway) with ESRI REST;
  initial service path `CritHab_HabEss_2025` was 404 — corrected to `dfo_sara_critical_habitat`;
  field names corrected (`COMMON_E`, `SCIENTIFIC`, `SARASTAT_E`); bbox tiles returned 0 features
  because `inSR=4326` was missing — layer is Web Mercator and the server was interpreting
  WGS84 degree values as metre coordinates
- **QC species ranges**: CKAN package lists FGDB resources before GeoJSON; `or` condition in
  resource selection matched the FGDB entry first; fixed to require both format==geojson AND
  fish keyword — resolves to `Aires_repartition_poisson_eau_douce.geojson`
- **CABIN benthic**: `AttributeError` on `v.strip()` when a CSV field value is `None`
  (csv.DictReader returns None for missing trailing columns); fixed with `v is not None` guard

### Bug fixes — session 6 (July 17 2026, autonomous Claude Code session)
Adapters marked ✅ above were tested live for the first time this session — none of
them had ever actually run against real data before.

- **NuSEDS (BC salmon escapement)**: download URL had gone stale (dated filename
  changes every DFO release) — now resolves dynamically via CKAN. Region-filter logic
  matched free-text keywords against what's now a numeric PFMA area code and matched
  nothing; dropped entirely (the file is BC-only, verified). Lat/lng columns no longer
  exist in the source; record key switched to NuSEDS's own ACT_ID (518 population/year/
  species composite collisions found — ACT_ID has none across all 421,001 rows).
- **BC regulations**: URL had gone stale. BC's region scheme changed (Region 7 split
  into 7A/7B, new Region 8 added) and the old splitter created one chunk per repeated
  page-header match instead of merging per region — upsert then kept only the last
  page, silently discarding most of each region's content. Also found a PDF rendering
  artifact (every character doubled in Region 7A's running header) and worked around
  it without needing to touch legitimate double letters elsewhere (Kootenay, Cariboo).
- **Alberta regulations**: was a stub assuming a ~100-WMU scheme; the real guide has
  only 3 Fish Management Zones split into 10 Watershed Units (ES1-4, PP1-2, NB1-4).
  Implemented against the real structure; URL resolved dynamically via CKAN (same
  pattern as NuSEDS) since a new dated PDF is published every year.
- **Alberta stocking**: real file has a variable-length title block before the header
  row and completely different column names than assumed; rebuilt. Also found and
  fixed a **crash bug**: `ingest_ab_stocking()` (and BC's `ingest_fiss_stocking()`,
  same bug) set `row["ingested_at"]` before upserting into `stocking_records`, which
  has no such column — every call would have raised `sqlite3.OperationalError` the
  moment this path was actually exercised.
- **Quebec species ranges**: was silently returning 0 records — GeoJSON property names
  didn't match the real file (`NOM_COMMUN_FR`/`NOM_SCIENTIFIQUE`/`STATUT_COSSEPAC`
  don't exist; real fields are `NOM_FRANCA`/`NOM_ANGLA`/`NOM_SCIENT`, and there's no
  conservation-status field in this file at all). Also: the source CRS is EPSG:32198
  (NAD83 / Quebec Lambert), a projected metre CRS, not WGS84 — centroids need
  reprojecting (added `pyproj`) or they look like coordinates but are nonsense
  (390200, -812965). Fixed a MultiPolygon centroid bug too (27 of 118 real features
  are MultiPolygon; the old code silently corrupted their centroids).
- **Cross-jurisdiction species collision (bigger than QC alone)**: `species_ranges` is
  shared across jurisdictions, keyed on common name — QC's `ingest_qc_species_ranges`
  and the federal `ingest_dfo_sar_range` both did a blind `upsert_all(pk="species")`.
  Running either for real would have overwritten the existing multi-jurisdiction
  "Largemouth Bass" row (CA-ON + 6 US states, with status fields set) down to just
  `["CA-QC"]` with nulled-out status, for every species QC/DFO share with the existing
  pool. Added `upsert_species_ranges_merged` (unions `jurisdictions_present`, preserves
  existing status/notes fields, appends rather than replaces `general_range`) and
  applied it to both ingest paths.
- **Critical bug — `jurisdiction_for_coords()` had no bounding boxes for CA-BC, CA-AB,
  CA-SK, CA-MB, CA-NS, CA-NB, or CA-PE.** This function backs jurisdiction tagging for
  every *global* adapter (iNaturalist, GBIF, OSM, weather, WSC, eBird) — not just the
  jurisdiction-specific ones. Every global-source observation for those seven
  provinces was silently tagged `jurisdiction='UNKNOWN'`, breaking jurisdiction-based
  filtering everywhere it's used, project-wide. Fixed by adding verified bounding
  boxes for all seven — checked against every `cron_area` in `config.py` plus known
  cities per province, since a first pass had real bugs (Calgary resolving to CA-BC,
  Miramichi resolving to CA-NS) caught before landing. File: `src/jurisdictions/geo.py`.
- **DataStream water quality**: query construction reviewed against DataStream's
  public OpenAPI schema and README — found it would have failed even with a valid
  key. The Locations→Records join used the wrong field (`Id`, an internal numeric
  field) instead of `ID` (the string station code Records.MonitoringLocationID
  actually matches); coordinates were read from nonexistent `LatitudeE7`/`LongitudeE7`
  fields (real fields are plain decimal `Latitude`/`Longitude`) — this silently
  produced lat=lng=0.0 for every station, which then got filtered by the existing
  "skip 0,0" check, so it would have looked exactly like "no stations found near you"
  rather than an obvious error; and the bbox filter used an unsupported
  `geo.intersects`/`geography'POLYGON(...)'` syntax instead of the documented plain
  numeric range filter. All fixed and unit-tested against mocked responses; live
  verification is pending the API key (see Pending above).
- **Admin dashboard**: new `GET /admin/dashboard` — see Admin dashboard section below.
- **Synthesis cache jurisdiction isolation**: see Synthesis cache section above.
- **BC EMS TODO**: corrected with real current resource info — see BC EMS section above.

## Coaching improvements 📋
Wait for data accumulation (need 10+ stops with time_of_day).
Currently only 3 stops have time_of_day — not enough for pattern detection.
Build when data is there:
- Time-of-day pattern detector
- Bait effectiveness detector
- Conditions-based pattern detection (need 10+ enriched sessions)
- Seasonal gap detector

## Prediction system ✅
Root cause of June 20 Willoway failure identified and fixed (June 27 2026).
Two compounding bugs:
1. get_tactical_recommendation always fetched when="now" regardless of whether
   the question was about tomorrow or the weekend — predictions were silently
   grounded in today's conditions.
2. Forecast responses (tomorrow/in_3_days/this_weekend) have no pressure trend
   data — even a correct fetch couldn't return pressure for future windows.

Fixes applied:
- get_tactical_recommendation now accepts `when` parameter, passes through to
  get_conditions_for_agent. For forecast windows, pressure call is skipped and
  a sentinel string is returned: "unavailable for forecasts — treat as neutral".
- Tool input schema updated — agent can now pass when="tomorrow" etc.
- System prompt updated: conditions routing rule explicitly requires the correct
  `when` value for future questions; cites June 20 as the reason.
- Synthesis cache bypassed for time-forward and live-conditions queries (keywords:
  tomorrow, this weekend, saturday, sunday, in 3 days, next week, forecast,
  right now, today, currently, at the moment, conditions, weather).
  Time-forward responses also excluded from cache writes.

Remaining gap: forecast windows still return no pressure trend. This is a data
availability limit (Open-Meteo doesn't provide pressure forecasts in the current
call), not a code bug. The agent is told to treat it as neutral and flag it to
the user.

## SDM improvements 📋
Current AUC 0.51-0.61 — honest but improvable.
Roadmap:
- Spatial thinning to reduce sampling bias
- Riparian canopy, impervious surface, agricultural intensity features
- Ensemble models (RF + MaxEnt + BRT)
- Integrated SDMs when enough trip log data exists

## Data expansion
Status by region:

### Ontario ✅
Grand River, Credit River, Bronte Creek, Thames (4 cron areas).
Full adapter coverage: OHN, PWQMN, MNRF stocking, MNRF regs, CABIN, GBIF, iNat, eBird, OSM.

### British Columbia ✅ (June 28 2026; tested live July 17 2026)
4 cron areas (Fraser, Thompson, Okanagan, Skeena).
FWA hydro + FISS observations + FISS stocking + BC regs PDF + NuSEDS salmon escapement
live and verified end-to-end (both regs and NuSEDS had gone stale/broken — see Bug
fixes — session 6).
EMS water quality: stations indexed, results stubbed (TODO corrected July 17 2026,
see EMS note above).

### Alberta 🔨 (June 28 2026; regulations completed + stocking fixed July 17 2026)
3 cron areas (Bow/Calgary, North Saskatchewan/Edmonton, Oldman/Lethbridge).
Stocking (planned dates XLSX) live — real file rebuilt to match actual structure,
does have coordinates (an earlier version of this note was wrong about that).
Regulations now implemented (10 watershed-unit chunks, not the ~100-WMU scheme
originally assumed).
Hydro network: FWMIS Simplified Hydro Arcs live 2026-10-09 (full implementation,
see Phase 2 — AB above). Fish observations: FWMIS SPECIES_PRES waterbody presence
live 2026-10-10 (authoritative survey presence, shared `observations` table).
Water quality still stubbed (no machine-readable source).
DataStream covers some AB watersheds via /ingest/data-national.

### Quebec 🔨 (June 28 2026; species_ranges fixed July 17 2026)
4 cron areas (St. Lawrence/Montreal, Saint-Maurice, Saguenay, Gatineau).
Species ranges (MELCCFP GeoJSON, 118 spp) live — was silently returning 0 records
before July 17 2026 (see Bug fixes — session 6); no COSEWIC status field actually
exists in the source, contrary to what this note used to say.
Regulations, water quality: stubbed (DataStream covers some QC watersheds).

### Manitoba + Saskatchewan 📋
3 cron areas each. Global sources only (iNat, GBIF, WSC, OSM).
DataStream covers some MB/SK watersheds via /ingest/data-national.
No province-specific adapters with public APIs found as of 2026.

### Maritimes (NS, NB, PEI) 📋
4 cron areas (Miramichi NB, Saint John NB, Annapolis NS, Margaree NS).
Global sources + CHS tidal API for coastal/tidal reach fishing.
DataStream covers Atlantic Canada watersheds.
No province-specific fish observation APIs found as of 2026.

### Federal sources ✅ (June 28 2026; DataStream query fixed July 17 2026)
DFO critical habitat, DFO SAR critical habitat (ESRI REST, no fiona), CHS tidal predictions
— built and verified working. DataStream water quality: query construction fixed
July 17 2026 (was broken in three ways that would have returned 0 results even with
a valid key — see Bug fixes — session 6); requires DATASTREAM_API_KEY, requested and
pending approval (see Pending above) — live path still unverified.

### US states 📋
Great Lakes region first: Michigan, Minnesota, Wisconsin.
- USGS NHD (equivalent of OHN)
- FishBase species ranges
- GBIF + iNat already global

### Saltwater 💡
East Coast, West Coast, Gulf.
- OBIS (Ocean Biodiversity Information System)
- NOAA data

## Voice trip logging 💡
Critical path for activating personal model at scale.
Reduces logging friction: speak → transcribe → parse → log.
Whisper API or similar.
Build after Lovable UI is stable.

## Admin dashboard ✅ (July 17 2026)
`GET /admin/dashboard`, X-Api-Key protected (src/services/admin_dashboard.py). Returns:
- Total users, messages sent (last 7/30 days + all-time)
- Top 10 queried locations (from segment_synthesis, the synthesis cache — the only
  table keyed on "what location did the user ask about")
- Tool call frequency + failure counts (from tool_usage)
- Ingest record counts by source and jurisdiction (observations, gbif_observations)
- Approximate Sonnet-vs-Haiku API cost estimate (all-time + last 30 days, from
  api_usage token counts)

Found during build: 100% of real synthesis-cache entries today are name-only (no
lat/lng) — a coordinate-only "top locations" query, which is what a first pass
produced, would always have returned empty. Fixed to also group name-only entries
by location name.

Extended July 18 2026 with catch_stats: total catches, top-10 species by count,
personal-bests summary (users with a recorded PB + top-10 largest fish by
biggest_size_cm), average catches per session. Fixed during build: by_species/
total_catches used COUNT(*) over catch rows instead of SUM(count) — a card logged
as "x3" only counted as 1, letting a real 3-fish catch rank below unrelated 1-fish
entries; also grouped on the raw species string instead of case-insensitively,
splitting "Shorthead Redhorse" (typed directly, no NL match) from "shorthead
redhorse" (NL-parsed) into two dashboard rows instead of merging like
personal_bests already does.

## Token management 📋
/admin/token endpoint added (X-Api-Key only, no Bearer needed).
fishbot token CLI command added.
Still friction: local DB ≠ Railway DB.
Accepted split for now.

## Aquarium feature 💡
Premium tier feature. Each species caught = fish added to aquarium.
Animated SVG/sprite fish swimming in a canvas.
More aquarium slots = premium conversion mechanic.
Shareable — the virality unlock.
Build after core product is solid and has users.

## Free/premium tier 💡
Router foundation in place (reflex vs synthesis).
Design pending:
- Free: reflex only (generic answers, no personal data)
- Premium: synthesis + memory + coaching + full map
- Taster: first synthesis free, deeper analysis gated

---

## Infrastructure

### Railway deployment 🔨
> Superseded 2026-10-09: Railway was dropped. The app is served with `make serve`
> and the weekly ingest runs locally with `make weekly-ingest` — see README.

URL: https://web-production-e2094.up.railway.app
Volume vol_wc8gnr3fyrcdcydx is attached but DATA_DIR env var is NOT set in
Railway dashboard. Database is writing to the container's ephemeral filesystem
and is wiped on every redeploy.
Fix: set DATA_DIR to the volume's mount path in Railway → service → Variables.
Once set, db_path in /admin/db-stats response will show an absolute path
confirming the volume is being used.

### Token refresh ✅
POST /admin/token with X-Api-Key returns fresh Bearer token.
No Bootstrap needed going forward.

### Local vs Railway split
Local: Jason's 22 sessions, all behavioral insights, full trip history.
Railway: Fresh database, beta tester data.
Accepted split — local for personal analysis, Railway for beta.

### /chat endpoint message assembly bug ✅ (June 27 2026)
Endpoint was reading body.get("messages", []) instead of building the messages
array from the documented schema fields (message + conversation_history).
Result: curl calls with {"message": "..."} produced an empty messages array,
causing "at least one message is required" from the Claude API.
Fix: endpoint now builds messages from history + appends current message.
make run was unaffected (CLI bypasses the HTTP endpoint entirely).
Deploy status: committed (067b616), Railway redeploy pending confirmation.

### FISS context overflow — fixed ✅ (June 29 2026)
46,294 FISS observations were being returned to Claude, exceeding context limits.
Root cause: query_observations had no LIMIT; all survey records returned regardless
of count. Fix: hard cap of 50 results (ORDER BY observed_on DESC), plus a separate
COUNT(*) query so total_count is included in the tool response. iNat records with
real dates sort first; FISS sentinel dates (1900-01-01) sort last.
Files: src/storage/observations.py, src/services/observations.py, src/services/gbif.py.

### /admin/db-stats diagnostic endpoint 🔨 (June 29 2026)
Temporary endpoint for Railway database diagnosis. Returns:
- db_path: resolved DB_PATH (shows whether relative or absolute)
- db_exists: bool — whether the file actually exists at that path
- data_dir_env: value of DATA_DIR env var, or "NOT SET"
- observations_by_source: count/lat-lng/date range per source (iNat, FISS, etc.)
- gbif_by_source: count/lat-lng/date range for gbif_observations table
Remove once Railway persistence is confirmed working.

### OSM 30-day cache skip ✅ (June 29 2026)
OSM ingest was causing Railway OOM when multiple cron areas ran concurrently —
Overpass requests backed up and memory built under rate limiting.
Fix: fetch_and_store() checks water_features for any rows within the 50km radius
with fetched_at >= 30 days ago. If found, returns DB counts immediately without
hitting Overpass. First weekly run fetches; all subsequent runs that week skip.
File: src/services/osm.py.

### /ingest/data days_back parameter ✅ (June 29 2026)
Added optional days_back body param (default 90). days_back=0 or null removes
the d1 date filter from the iNat API call, pulling all historical observations.
Useful for sparse areas (MB, SK, prairies) where the 90-day window returns 0.
Passes through: endpoint → _run_global_ingest → fetch_and_store → fetch_observations.
To run a historical backfill:
  POST /ingest/data {"lat":49.90,"lng":-97.14,"radius_km":50,"days_back":0,"label":"winnipeg-historical"}

### iNat API pagination cap — known limit (June 29 2026)
The iNat v1 API serves at most 10,000 records per query regardless of total_results.
With per_page=200, max fetchable = 50 pages × 200 = 10,000. For queries where
total_results > 10,000, the fetcher exits on an empty page but silently under-ingests.
A WARNING is now logged: "iNat: fetched N of M total — API pagination cap reached,
X records unreachable". No workaround — this is an iNat API constraint. Affects
densely-observed ON/BC areas more than sparse prairies.
File: src/ingest/global/inaturalist.py.

### Known dead code
ChatRequest Pydantic model (src/api/main.py:154) is unused — endpoint takes
body: dict directly. Harmless but should be cleaned up or wired in.

### GitHub Actions weekly ingest ✅
Sunday 6am UTC. 25 cron areas total (June 28 2026):
- 4 ON — hit /ingest/data
- 4 BC — hit /ingest/data + /ingest/data-bc
- 3 AB — hit /ingest/data + /ingest/data-ab
- 4 QC — hit /ingest/data + /ingest/data-qc
- 3 MB + 3 SK — hit /ingest/data only
- 4 Maritimes (Miramichi NB, Saint John NB, Annapolis NS, Margaree NS) — hit /ingest/data
- 2 tidal (Miramichi NB, Annapolis NS) — additionally hit /ingest/data-tidal

### Ingest background tasks ✅ (June 28 2026)
Both /ingest/data and /ingest/data-bc return 202 immediately via FastAPI BackgroundTasks.
Prevents Railway from killing the connection during long ingest runs.
Each source logs start, record count, and full traceback on error (logger.exception).

### Railway logging root cause fixed ✅ (June 28 2026)
Background task INFO messages were silently dropped — uvicorn's dictConfig leaves the
root logger at WARNING. Fixed: logging.basicConfig(stdout, INFO) at module import +
lifespan re-asserts setLevel(INFO) after uvicorn's own setup runs.

---

## Open research questions

### Lake-run channel catfish timing — Dunnville
When do lake-run Erie cats push up the Grand?
Track date + fish size + water temp for every Dunnville session.

### Bowfin / channel catfish ratio — Willoway
June 20: channels dominant. Prior session: bowfin dominant.
Hypothesis: conditions-driven. Need retroactive enrichment of prior session.

### Kool-aid vs natural cutbait
Controlled comparison planned: same location, same rig, one rod each.
Run at Byng or Willoway next session.

---

## Key credentials (save these)
- Railway API key: fdf0f9f1381176065637fe72a69cd651bb6ac117cbcabcf60d26988127a574a9
- Railway URL: https://web-production-e2094.up.railway.app
- Admin token endpoint: POST /admin/token with X-Api-Key header
- GitHub repo: GuaiDev/fishbot (rename to fishdex when ready)
