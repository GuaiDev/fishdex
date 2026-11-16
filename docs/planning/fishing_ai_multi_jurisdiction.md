# Fishing AI Bot — Multi-Jurisdiction Correction

Supersedes Ontario-specific framing in the four previous planning docs. The core architecture, phases, and feature catalog remain valid — but data sources, schema, and prompts need to be jurisdiction-aware from Day 0.

**TL;DR:** Build it generic, populate it with Ontario first (you live there), expand to other Canadian provinces and US states by adding data adapters — not by rewriting the bot.

---

## 1. The One Architectural Change

Every other change in this document follows from this single design decision: **`jurisdiction` is a first-class concept throughout the system.**

### What this means concretely

- Every record that's location-bound has a `jurisdiction` field (e.g., `CA-ON`, `CA-BC`, `US-MI`, `US-NY`). Use ISO 3166-2 codes — they're standard and unambiguous.
- Data ingestion modules are organized by jurisdiction: `src/ingest/jurisdictions/ca_on/`, `src/ingest/jurisdictions/us_mi/`, etc.
- Generic ingestion modules that work everywhere (iNaturalist, GBIF, OSM, Sentinel-2) live at `src/ingest/global/`.
- The user's profile has a `home_jurisdiction` and a `frequented_jurisdictions` list.
- The bot's system prompt dynamically loads regulatory context for the relevant jurisdiction based on where the user is asking about, not where they live.

### Jurisdiction registration

For the current registration contract, see the [jurisdiction checklist](../../src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md).

### Graceful fallback for unknown jurisdictions

When you don't have data for a state/province yet, the bot should:
1. Use **global** datasets (iNaturalist, GBIF, Sentinel-2, OSM, Microsoft Building Footprints)
2. Tell the user clearly: "I don't have detailed regulations or stocking data for Vermont yet. Verify rules with the Vermont Fish & Wildlife Department before fishing."
3. Log the gap so you know which jurisdictions to fill in next.

This way the bot works *everywhere* on day one, just less deeply in places you haven't populated yet.

---

## 2. Updated Data Source Catalog

The Ontario-specific sources from prior docs are now one entry in a larger table. Generic global sources do most of the work.

### Globally applicable (works anywhere — build these first)

| Data | Source | Notes |
|---|---|---|
| Species observations | **iNaturalist API** | Already in plan. Worldwide. |
| Species occurrence aggregation | **GBIF** | Aggregates iNaturalist + museum + survey records globally |
| Hydrology (basic) | **HydroSHEDS** | Global, free, lower resolution. Fallback when local data missing. |
| Hydrology (continental detail) | **HydroLAKES** + **HydroRIVERS** | Better than HydroSHEDS for lakes/rivers, still global |
| Satellite imagery (multispectral) | **Sentinel-2** | Global, 10m, free, refreshed every 5 days |
| Satellite imagery (historical) | **Landsat 8/9** | Global, 30m, free, decades of archive |
| Water extent / surface water | **JRC Global Surface Water** | Every water body on Earth with seasonality |
| Land cover | **Dynamic World** | Near-real-time global land cover incl. water |
| Building footprints | **Microsoft Global ML Building Footprints** | Free, all of Canada + US + many other countries |
| Roads, trails, place names | **OpenStreetMap** | Global, community-maintained |
| Weather | **Open-Meteo** | Free, global, no key required |
| Elevation (DEM) | **SRTM** (global 30m) or **Copernicus DEM** (global 30m) | Free, global |
| Water levels (rivers) | **GRDC** for global; agency-specific for detail | Global Runoff Data Centre |
| Climate / hydrologic patterns | **NASA POWER**, **NOAA Climate Data** | Global, free |

These thirteen sources alone get you a working bot anywhere in North America. Everything below adds depth.

### Canada — Federal (works across all provinces)

| Data | Source |
|---|---|
| Comprehensive hydrography | **National Hydro Network (NHN)** — Natural Resources Canada |
| Aquatic species occurrences | **Fisheries and Oceans Canada (DFO)** open data |
| Aquatic barriers (dams, culverts) | **DFO Aquatic Barriers Database** |
| Species at Risk | **SARA (Species at Risk Act) Registry** |
| Protected areas | **CPCAD (Canadian Protected and Conserved Areas Database)** |
| Navigable waters | **DFO Navigable Waters Public Register** |
| Stream gauges | **Water Survey of Canada (wateroffice.ec.gc.ca)** |
| Tides (coastal) | **CHS (Canadian Hydrographic Service)** |
| Bathymetry — Great Lakes | **NOAA Great Lakes** (binational coverage) |

### Canada — Provincial (Ontario first, others as templates)

**Ontario** (your home base, build first):
- MNRF Fish Stocking, regulations, Broadscale Monitoring, Ontario Hydro Network, SWOOP imagery, Crown Land Atlas, Aquatic Habitat Inventory, Fish ON-Line

**Other provinces — equivalents to build later:**

| Province | Stocking/Regs | Imagery | Hydrography |
|---|---|---|---|
| **British Columbia** | Freshwater Fisheries Society of BC; BC Fishing Regulations Synopsis | TRIM, provincial orthos | iMapBC |
| **Alberta** | Alberta Environment and Parks; AEP fishing regulations | SPOT, provincial orthos | AltaLIS |
| **Saskatchewan** | Saskatchewan Ministry of Environment | Provincial orthos | GeoSask |
| **Manitoba** | Manitoba Sustainable Development | Manitoba Land Initiative | Manitoba Hydro Network |
| **Quebec** | Société des établissements de plein air du Québec (Sépaq); MFFP | Photos aériennes du Québec | Adresses Québec |
| **New Brunswick** | NB Department of Energy and Resource Development | GeoNB | NB hydrography |
| **Nova Scotia** | NS Inland Fisheries Division | NS Topographic Database | NS hydrography |
| **PEI** | PEI Department of Environment | PEI imagery | PEI hydrography |
| **Newfoundland & Labrador** | NL Department of Fisheries | NL imagery | NL hydrography |
| **Yukon/NWT/Nunavut** | Territorial governments | Limited | NHN base |

Don't try to ingest all of these. Build the Ontario pipeline first; the others become copy-paste-and-adjust exercises *after* the architecture proves itself.

### United States — Federal (works across all 50 states)

The US federal data layer is genuinely excellent and in many ways exceeds what Canada provides federally:

| Data | Source | Notes |
|---|---|---|
| Comprehensive hydrography | **NHD (National Hydrography Dataset)** | USGS. The gold standard. Better than anything Canada has at federal level. |
| High-resolution aerial imagery | **NAIP (National Agriculture Imagery Program)** | USDA. 1m resolution, all 50 states, refreshed every 2–3 years. Free. Comparable to SWOOP. |
| Building footprints | **Microsoft US Building Footprints** | Free, all 50 states |
| Stream gauges | **USGS Water Data for the Nation** | Real-time, comprehensive |
| Bathymetry — coastal | **NOAA NCEI** | High-quality coastal soundings |
| Bathymetry — large inland lakes | **NOAA Great Lakes**, **USGS** | Comprehensive for Great Lakes |
| Endangered species | **US Fish & Wildlife ECOS** | Federal listings |
| Public land — vast western US | **BLM (Bureau of Land Management)** | Millions of acres open to public access |
| Public land — National Forests | **USFS** | Mostly open to fishing |
| National Parks | **NPS** | Variable fishing rules |
| Land cover | **NLCD (National Land Cover Database)** | High quality |
| Elevation | **USGS 3DEP** | 1m LiDAR in many areas; 10m elsewhere |
| Climate | **PRISM Climate Group** | Excellent gridded climate data |
| Fish habitat | **NOAA Fisheries** + **USFWS** | Various habitat datasets |
| Aquatic invasives | **USGS NAS (Nonindigenous Aquatic Species)** | National database |

### United States — State

Every state has a fish and wildlife agency. They vary wildly in data quality and accessibility. Some publish excellent open data (Michigan, Wisconsin, Minnesota, Washington); others publish PDFs only.

**Strategy:** don't try to handle all 50 states up front. Build a generic state-jurisdiction template that:
1. Fetches regulations PDF/URL from a registry
2. Parses what it can with Claude
3. Falls back to "contact this state's agency" for what it can't

Then fill in detailed adapters state-by-state as you actually fish those waters.

**Priority US states based on overlap with Canadian anglers + great open data:**
- **Michigan** (Michigan DNR — excellent open data; Great Lakes fishing)
- **Minnesota** (MN DNR LakeFinder — best lake database in North America)
- **Wisconsin** (WDNR — strong open data)
- **New York** (NYSDEC — adjacent to Ontario)
- **Vermont** (VTFW)
- **Maine** (MEDIFW — adjacent to Canadian Maritimes)
- **Washington** (WDFW — excellent for Pacific fishing)

---

## 3. Specific Corrections to Prior Docs

Rather than rewrite every doc, here's what to mentally replace as you read them.

### Base Roadmap (`fishing_ai_roadmap.md`)

- **"Phase 3 — Government & Regulatory Data"** section: where it says "For Ontario specifically" — that's now "For your home jurisdiction first, then expand." The MNRF/Fish ON-Line specifics become the *Ontario adapter*, not the whole phase.
- **"Toronto-specific advantage"** note in chat summary: still applies as a starting point, but the architecture supports expansion from day one.

### Advanced Capabilities (`fishing_ai_advanced_capabilities.md`)

- **Hydrological network analysis** (Section 1) — replace "Ontario Hydro Network" with "your jurisdiction's best hydrography source." For US users this is NHD. For other Canadian provinces, the equivalent provincial dataset. For unknown jurisdictions, fall back to NHN/HydroSHEDS.
- **Barrier data** — for US use the **National Anthropogenic Barrier Dataset** and **NID (National Inventory of Dams)** in addition to provincial sources.
- **MNRF Broadscale Monitoring** specifically — that's an Ontario asset. US equivalents vary by state but Minnesota DNR LakeFinder, Wisconsin DNR Surface Water Data, and Michigan DNR Inland Lakes data are comparable.
- **Habitat-based SDM** (Section 5) — no change. The modeling approach is jurisdiction-agnostic; only the input data sources change.

### Spot Discovery (`fishing_ai_spot_discovery.md`)

- **SWOOP imagery** — Ontario only. Replacements by region:
  - Other Canadian provinces: each has provincial orthophoto programs (TRIM, etc.)
  - All US: **NAIP** (1m, every 2–3 years, free, nationwide). This is your default for US users and is excellent.
  - Global fallback: Sentinel-2 (10m)
- **Crown Land Use Policy Atlas** — Ontario only. Replacements:
  - Other provinces: each has Crown Land mapping
  - US: **BLM National Public Land Survey System** for federal public land, plus state-specific layers
- **Conservation Authority boundaries** — Ontario-specific institution. US equivalent: state Wildlife Management Areas (WMAs), federal Wildlife Refuges, National Forests
- **Provincial Parks** — replace with "Provincial Parks (Canada) or State Parks (US) and National Parks (both)"
- **MPAC parcel data** — Ontario only. Other regions have their own parcel layers; US has county-level parcel data of varying quality

### Feature Catalog (`fishing_ai_feature_catalog.md`)

- **Section 10 — Regulatory & Legal:** every reference to MNRF and Ontario zones generalizes. Add explicitly:
  - "Bot must know which jurisdiction governs the water body the user is asking about — sometimes regulations differ by lake even within a state"
  - "Border waters (US-Canada): Lake of the Woods, St. Clair, St. Lawrence, etc. — these have special binational rules"
  - "Tribal/First Nations waters: separate jurisdiction. Bot should flag and direct user to relevant tribal authority."
- **Section 7 — Access & Logistics:** generalize "MNRF launch database" to "jurisdiction's boat launch registry." US has state-published launch lists for most states.

### Getting Started Guide (`fishing_ai_getting_started.md`)

This needs the most direct correction since it's what you're about to act on. Specific updates below in Section 4.

---

## 4. Updated Getting Started Prompts

These replace the corresponding prompts in the original Getting Started doc.

### Updated Prompt 1 — Onboarding

> Read every file in `docs/planning/` (including the multi-jurisdiction correction). These are my planning documents for a personalized fishing AI bot I'm building for myself. I live in Toronto, Ontario, but I want to be able to fish anywhere in Canada or the US — so the bot should be architected jurisdiction-agnostically with Ontario as my first populated jurisdiction. After reading, summarize back to me in 5 bullets: (1) what we're building, (2) the phased approach, (3) the unique angles vs existing fishing apps, (4) the tech stack we agreed on, (5) what Phase 0 and Phase 1 specifically require — emphasizing the jurisdiction abstraction. Don't write any code yet.

### Updated Prompt 3 — CLAUDE.md

> Create a CLAUDE.md for this project at the repo root. Keep it under 100 lines. It should include:
> 
> (1) What the project is in 2 sentences — a personal fishing exploration bot for use across Canada and the US, with Ontario as the first populated jurisdiction.
> 
> (2) Tech stack and key dependencies.
> 
> (3) My fishing context: home base Toronto, Ontario; primary target species smallmouth bass, brook trout, pike, walleye; primary fishing style stream + small lakes; skill level intermediate; primary use case exploration over optimization. Will travel to other provinces and US states.
> 
> (4) Architectural conventions: **jurisdiction-aware design throughout**. Every location-bound record carries a jurisdiction (ISO 3166-2 code: CA-ON, US-MI, etc.). Jurisdictional data sources live in `src/ingest/jurisdictions/<code>/`. Global sources (iNaturalist, OSM, Sentinel-2) live in `src/ingest/global/`. Unknown jurisdictions fall back to global sources with a "limited data" disclaimer.
> 
> (5) Project conventions: uv for deps, ruff for linting, pytest for tests, conventional commits.
> 
> (6) How to run things: make run, make test, make ingest.
> 
> (7) Pointers to docs/planning/ as the canonical product spec, with the multi-jurisdiction correction superseding any Ontario-specific framing in the older docs.
> 
> Do NOT duplicate planning doc content — reference it by filename. Every line should be something you'd consult while coding.

### Updated Prompt 4 — MVP Bot (Phase 1)

> Implement Phase 1 from the base roadmap: a minimum viable CLI fishing bot. 
> 
> Critical: this must be jurisdiction-aware from Day 0.
> 
> Requirements:
> - Use typer for the CLI with commands: chat, log (log a trip), recent (show recent trips), profile (view/edit user profile).
> - Use the Anthropic SDK with claude-sonnet-4-6 by default, env-var overridable.
> 
> - User profile JSON schema at data/user_profile.json:
>   - home_jurisdiction (ISO 3166-2, e.g., "CA-ON")
>   - frequented_jurisdictions (list of ISO codes)
>   - home_location (lat/lng + name)
>   - target_species (list)
>   - gear (list of dicts)
>   - budget (annual)
>   - skill_level
>   - fishing_style
>   - preferences (free text)
> 
> - SQLite trips schema in data/fishing.db: id, date, jurisdiction (ISO 3166-2), location_name, lat, lng, species_caught (json), conditions (json), gear_used (json), notes, what_worked, what_didnt.
> 
> - For jurisdiction registration, follow the [jurisdiction checklist](../../src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md).
> 
> - Chat loop: load user profile + 5 most recent trips + active jurisdiction context into the system prompt every turn. System prompt at prompts/system.md.
> 
> - System prompt must instruct the bot to: identify which jurisdiction governs the water body in question, load that jurisdiction's regulatory context, and explicitly flag when working with an unpopulated jurisdiction ("I don't have detailed regulations for Vermont yet — verify with VTFW before fishing").
> 
> - rich for terminal output. Tests for storage layer and jurisdiction registry.
> 
> Verification: after implementation, run make test, then make run and demonstrate (a) chatting about Ontario fishing with Ontario context loaded, (b) chatting about Michigan fishing with the bot correctly identifying it's working with a limited-data jurisdiction.
> 
> Write the plan first.

### Updated Prompt 5 — iNaturalist (Phase 2)

> Implement Phase 2: iNaturalist ingestion.
> 
> iNaturalist is a global data source so it lives at src/ingest/global/inaturalist.py. The function fetch_observations should accept a bounding box (not assuming any jurisdiction) and return observations tagged with the jurisdiction they fall within (use a reverse-geocode lookup or a shapefile of jurisdiction boundaries).
> 
> Persist to an observations table in fishing.db with jurisdiction column.
> 
> CLI command make ingest pulls observations for a configurable bounding box (default: user's home_location + 50km, but accept overrides for other regions).
> 
> Wire as a tool the chat agent can call: get_recent_observations(lat, lng, radius_km, days_back, species_filter?, jurisdiction?). The jurisdiction parameter is optional — if provided, filters to that jurisdiction.
> 
> Verification: ingest observations for both an Ontario region and a Michigan region, then ask the bot questions about both. Confirm it correctly attributes observations to jurisdictions.
> 
> Write the plan first.

---

## 5. The Order of Population

Don't try to populate every jurisdiction at once. Suggested order based on your stated travel patterns and data quality:

1. **CA-ON (Ontario)** — your home, best data familiarity, build first
2. **US-MI (Michigan)** — excellent open data, adjacent to Ontario, Great Lakes overlap
3. **US-NY (New York)** — adjacent, good open data, shared waters
4. **CA-QC (Quebec)** — adjacent, good Sépaq data
5. **US-MN (Minnesota)** — best lake database in North America, worth modeling on
6. **US-WI (Wisconsin)** — strong open data
7. **CA-BC (British Columbia)** — if you travel west
8. **Everything else as you actually plan trips there**

Each new jurisdiction is mostly a copy-paste-and-adjust of the previous one once the abstraction is solid. The Ontario adapter is your reference implementation; everyone else mimics it.

---

## 6. Where the Scope Change Helps You

A few benefits that aren't obvious:

- **NAIP imagery is uniform across all of the US.** SWOOP only covers Southern Ontario. Once you cross into the US, you actually get *more consistent* high-res imagery than you do across Canada.
- **NHD is more complete than NHN.** Building stream networks for US states is in some ways easier than for Canada's western provinces.
- **BLM and National Forest lands** in the western US are *enormous* areas of public-access land — the "untapped potential" use case is actually richer there than anywhere in Eastern Canada.
- **Minnesota's LakeFinder** is the model of what every state/province *should* publish. Studying it will tell you what to build for jurisdictions with weaker data.
- **US tournament data** (BASS, FLW, MLF) is extensive and publicly available. Tournament patterns are gold for tactical recommendations.
- **For Great Lakes fishing**, you really need both Canada and US data anyway — the fish don't care about borders.

---

## 7. Where the Scope Change Costs You

Being honest:

- **Regulations complexity explodes.** 1 provincial reg set → 50+ jurisdictional reg sets. Hard cap on how many you can deeply support.
- **Stocking data formats vary wildly.** Each agency publishes differently. Adapter per jurisdiction takes ~half a day each.
- **Imagery normalization.** SWOOP, NAIP, Quebec orthos, BC TRIM — all different formats, projections, refresh schedules. The processing pipeline has to handle them.
- **Species lists differ.** A "trout" in Newfoundland is different from a "trout" in Arizona. Need a canonical species ID system (use ITIS TSN or GBIF taxonKey).
- **Time zones and units.** US uses imperial; metric elsewhere. UI must adapt.
- **Testing surface area grows.** Each jurisdiction is a potential edge case.

The architecture handles all of this if you build the abstraction right from Day 0. Trying to retrofit it later is genuinely painful — schema migrations, code refactors, every test rewritten.

---

## 8. The One Sentence You Should Remember

**Build the bot jurisdiction-agnostic, populate Ontario first, expand by adding adapters — not by rewriting.**

If you do this from Day 0, scaling from 1 jurisdiction to 30 is mostly mechanical work. If you don't, it's a full architectural rewrite. Cheap insurance.
