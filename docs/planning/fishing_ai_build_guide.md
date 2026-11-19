# Fishing AI Bot — The Complete Build Guide

The consolidated, opinionated, step-by-step plan for vibe coding this in Claude Code. Supersedes scattered guidance across the six prior planning docs. Reads top-to-bottom on Day 1; revisited section-by-section as you go.

**Scope:** personal fishing exploration bot. Jurisdiction-aware (Canada + US). Built on open, legal, ethical data sources. Designed to surface original insight rather than redistribute other anglers' private knowledge.

---

## Part I — Principles

These are the rules that make everything else work. If you skip the rest of the doc, don't skip this.

### 1. The five non-negotiables

1. **Build for yourself first.** Public release is a future decision. Until then, this is your personal tool and every feature is judged on whether *you'd* use it.
2. **Exploration over optimization.** The bot's purpose is to find new water and deepen your understanding of watersheds — not to maximize catches at known spots.
3. **Original insight, not extraction.** The bot generates knowledge from open data, hydrology, habitat models, and your own logged trips. It does not extract other anglers' private spot information from social media, scraped apps, or de-anonymized video.
4. **Jurisdiction-aware from Day 0.** Every location-bound record carries an ISO 3166-2 jurisdiction code. Data sources organize by jurisdiction. The bot works globally with graceful fallback where local data is thin.
5. **Trip log discipline above all.** Every outing logged, every time, with what worked and what didn't. This is the dataset that makes the bot uniquely yours.

### 2. The ethical bright lines

These are decided. Don't relitigate them while coding tired.

- **No Instagram, Facebook, TikTok scraping.** ToS-violating, legally risky, ethically corrosive, and not necessary.
- **No FishBrain, FishAngler, or other paid-app scraping.** Same issues plus active anti-scraping enforcement.
- **No image-based de-anonymization of strangers' fishing locations.** Vision analysis is for *your own* footage and for *terrain analysis you do yourself*.
- **No publishing or exporting the spot-discovery output.** A tool that automatically reveals every quiet farm pond in a region can destroy those fisheries within a season. Keep discoveries personal.
- **Respect explicit "don't share this spot" statements** in any content you ingest. If an angler says they're keeping a location private, honor it.
- **Indigenous waters are separate jurisdiction.** Flag and defer; do not attempt to predict or recommend within them.

### 3. The legitimate data sources (the canonical list)

This is what you build on. Everything else is either redundant or off-limits.

**Global (work anywhere):**
- iNaturalist, GBIF — species observations
- OpenStreetMap — roads, trails, water, places, public-land tags
- Sentinel-2, Landsat — satellite imagery, free
- Microsoft Global ML Building Footprints — accessibility filtering
- JRC Global Surface Water, Dynamic World — water extent
- HydroSHEDS, HydroLAKES, HydroRIVERS — hydrography fallback
- Open-Meteo — weather
- SRTM / Copernicus DEM — elevation
- NASA POWER, NOAA Climate — long-term climate

**Canada (federal):**
- National Hydro Network (NHN) — comprehensive hydrography
- DFO open data — aquatic species, barriers, navigable waters
- Water Survey of Canada — real-time stream gauges
- CHS — coastal hydrography, tides
- SARA Registry — species at risk
- CPCAD — protected areas

**Canada (provincial — Ontario first, others as you expand):**
- MNRF: stocking, regulations, Broadscale Monitoring, Ontario Hydro Network, SWOOP imagery, Crown Land Atlas, Aquatic Habitat Inventory, Fish ON-Line
- Conservation Authority data (Credit Valley, TRCA, Halton, etc.)
- (Other provincial equivalents — see Multi-Jurisdiction doc for the table)

**US (federal):**
- USGS NHD — comprehensive hydrography, gold standard
- USDA NAIP — 1m aerial imagery, all 50 states, free
- USGS Water Data — real-time stream gauges
- NOAA — Great Lakes bathymetry, coastal data
- BLM, USFS, NPS — federal public-land boundaries
- NLCD, USGS 3DEP — land cover, elevation
- USFWS ECOS — endangered species
- USGS NAS — aquatic invasives
- PRISM — gridded climate

**US (state — start with high-value adjacent states):**
- Michigan DNR, Minnesota DNR LakeFinder, Wisconsin DNR, New York DEC
- (Other states as you actually fish them)

**Community content (where ToS permits):**
- Reddit — public API, respectful use
- YouTube — public API, transcripts via youtube-transcript-api
- Public fishing forums — robots.txt + polite scraping
- Guide service and outfitter weekly reports — published *for* public consumption
- Tournament results (BASS, FLW, MLF, regional) — public-domain results
- News and magazine archives — published, citable
- iNaturalist (already global) — opt-in citizen science

**Your own data:**
- Trip log (the most valuable dataset you'll ever generate)
- Personal Instagram/Facebook/Strava/Garmin exports (your own archive)
- Photos, sonar logs (GPX from Lowrance/Garmin/Humminbird) if you own a unit
- Field notes, voice memos

That's the universe. If a source isn't on this list, the default answer is no.

---

## Part II — The Architecture in One Page

```
┌──────────────────────────────────────────────────────────────┐
│ User Interface                                                │
│   - CLI (typer)  → Phase 1                                    │
│   - Map UI (Next.js + Leaflet) → Phase 6                      │
│   - Mobile/PWA → eventual                                     │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ Agent Layer                                                   │
│   - Anthropic SDK (claude-sonnet-4-6 default)                 │
│   - System prompt: user profile + recent trips + jurisdiction │
│   - Tools: query DB, fetch conditions, run predictions, etc.  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ Domain Services                                               │
│   - Trip log         - Conditions       - Lure recommender    │
│   - Hydrology graph  - Habitat SDM      - Spot discovery      │
│   - Access scorer    - Regulations      - Continuous learner  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ Data Layer                                                    │
│   - SQLite + SpatiaLite (dev) / PostGIS + pgvector (later)    │
│   - Cache layer (every external call cached)                  │
│   - Files: raw/, processed/, models/                          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│ Ingest Adapters                                               │
│   src/ingest/global/      (iNaturalist, OSM, Sentinel, etc.)  │
│   src/ingest/jurisdictions/<code>/  (ON, MI, NY, ...)         │
│   src/ingest/community/   (reddit, youtube, forums, reports)  │
│   src/ingest/personal/    (your own exports, sonar, photos)   │
└──────────────────────────────────────────────────────────────┘
```

Three principles encoded above:
1. The agent talks to **services**, not raw data. Services hide jurisdiction differences from the LLM.
2. Ingest adapters are pluggable. Add a new jurisdiction = add a new adapter, don't touch the agent.
3. Everything caches. External APIs are hit once and stored.

---

## Part III — The Phased Build Plan

Eight phases. Each phase ends with something you can use. Phase 1 alone is more useful than most fishing apps you've tried.

### Phase 0 — Project Setup (½ day)
**Outcome:** clean repo, tooling working, Claude Code oriented.

### Phase 1 — MVP Bot with Trip Log (2–3 days)
**Outcome:** CLI bot that knows you, remembers trips, answers basic questions.

### Phase 2 — Global Data Foundations (1 week)
**Outcome:** iNaturalist + OSM + weather + jurisdiction registry working. Bot can answer "what's been observed near X recently."

### Phase 3 — Tactical Help-Buddy (1 week)
**Outcome:** lure/bait/technique recommender. The daily-use feature.

### Phase 4 — Government & Regulatory Data (1–2 weeks)
**Outcome:** Ontario regulations + MNRF + stocking history loaded. Generalized for adding states next.

### Phase 5 — Hydrological Network Analysis (2–3 weeks)
**Outcome:** stream connectivity graph for your home watershed. The differentiator.

### Phase 6 — Spot Discovery from Satellite + Accessibility (3–4 weeks)
**Outcome:** "find me untapped water bodies within X km that I can legally reach." The other differentiator.

### Phase 7 — Habitat-Based Species Prediction (1–2 weeks)
**Outcome:** species probability per stream segment / water body. Ties Phase 5 + 6 + Phase 2 data together.

### Phase 8 — Map UI + Voice + Offline (3–4 weeks)
**Outcome:** the thing you'd actually pull out on the water.

**Ongoing:** community content RAG, continuous improvement loop, more jurisdictions, more features from the catalog.

**Realistic total to Phase 8:** ~3–4 months of evenings and weekends. Genuinely usable by week 2.

---

## Part IV — Step-by-Step Execution

This is the part you actually do. Read once, then work through it in order.

### Pre-flight: Install everything

**Claude Code** (native installer, current 2026 recommendation):
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
Verify: `claude --version`.

Requires a paid Claude account (Pro at $20/month is plenty).

**Python and uv:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Git, if you don't have it:**
- macOS: `xcode-select --install`
- Linux: your package manager
- Windows: install WSL2 first, then run everything inside WSL

**Create the project:**
```bash
mkdir fishing-bot && cd fishing-bot
git init
mkdir -p docs/planning
```

Copy all six planning markdown files into `docs/planning/`:
- `fishing_ai_roadmap.md`
- `fishing_ai_advanced_capabilities.md`
- `fishing_ai_spot_discovery.md`
- `fishing_ai_feature_catalog.md`
- `fishing_ai_multi_jurisdiction.md`
- `fishing_ai_getting_started.md` (and this doc, once you have it)

---

### Phase 0 — Project Setup

#### Step 0.1 — Launch Claude Code and orient it

```bash
claude
```

First time, it opens a browser for OAuth. Authenticate.

Then paste this exact prompt:

> Read every file in `docs/planning/`. These are my planning documents for a personalized fishing AI bot.
> 
> Key context to internalize:
> - I'm building this for myself first, public release is a later decision
> - I live in Toronto, Ontario but want it to work across Canada and US
> - Architecture must be jurisdiction-aware from Day 0 (ISO 3166-2 codes throughout)
> - Exploration is the north star, not catch optimization
> - Strict ethical framework: no scraping of Instagram/Facebook/FishBrain, no de-anonymizing strangers' content, no extracting others' private spots
> - Build on open, legal, ethical data sources only
> 
> After reading, summarize in 6 bullets:
> 1. What we're building (one sentence)
> 2. The 8-phase plan at a glance
> 3. The non-negotiable principles
> 4. The ethical bright lines
> 5. The legitimate data source categories
> 6. What Phases 0 and 1 specifically require
> 
> Don't write any code yet. If anything in the docs contradicts what I just told you, flag it.

Read the summary carefully. If anything's wrong, correct it now — this becomes the shared frame for everything that follows.

#### Step 0.2 — Plan the scaffold

Enter plan mode (`Shift+Tab` twice). Paste:

> Set up Phase 0 from the consolidated plan.
> 
> Use uv for package management, Python 3.11+. Create this structure:
> 
> ```
> fishing-bot/
> ├── data/
> │   ├── raw/
> │   ├── processed/
> │   ├── cache/
> │   ├── models/
> │   └── fishing.db (created on first run)
> ├── docs/planning/  (already exists)
> ├── prompts/
> │   └── system.md
> ├── src/
> │   ├── agent/           # Anthropic SDK + tool definitions
> │   ├── cli/             # typer commands
> │   ├── ingest/
> │   │   ├── global/      # iNaturalist, OSM, Sentinel, etc.
> │   │   ├── jurisdictions/   # ca_on/, us_mi/, etc.
> │   │   ├── community/   # reddit, youtube, forums
> │   │   └── personal/    # own exports, sonar
> │   ├── jurisdictions/   # registry + base class
> │   ├── models/          # pydantic schemas
> │   ├── services/        # domain logic
> │   └── storage/         # SQLite wrappers
> ├── tests/
> ├── Makefile
> ├── pyproject.toml
> ├── .env.example
> ├── .gitignore
> └── CLAUDE.md (next step)
> ```
> 
> pyproject.toml dependencies (minimum to start): anthropic, httpx, sqlite-utils, pydantic, python-dotenv, rich, typer, pytest, ruff.
> 
> Makefile targets: `run`, `ingest`, `test`, `lint`, `format`.
> 
> .gitignore must exclude .env, data/cache/, data/raw/, data/fishing.db, __pycache__, .pytest_cache, .ruff_cache.
> 
> .env.example includes ANTHROPIC_API_KEY (with comment that Claude Code reuses your auth so this is for SDK use), CLAUDE_MODEL=claude-sonnet-4-6.
> 
> Don't write business logic yet — pure skeleton only. Write me the plan first.

Review the plan. Watch for: scope creep (don't accept "I'll also add X"), missing files (every directory listed should appear), reasonable dependencies (push back on anything not needed yet).

When the plan is right, let it execute.

#### Step 0.3 — Generate CLAUDE.md

> Now create CLAUDE.md at the repo root. Keep under 100 lines. Structure:
> 
> ## Project
> Use the current project status from [CLAUDE.md](../../CLAUDE.md#project); do not copy this historical setup guide as the release policy.
> 
> ## My fishing context
> - Home: Toronto, Ontario (CA-ON)
> - Will travel: anywhere in Canada and US
> - Target species: smallmouth bass, brook trout, pike, walleye
> - Style: stream + small lakes, primarily
> - Skill: intermediate
> - Use case: exploration over optimization
> 
> ## Tech stack
> [list]
> 
> ## Architectural rules
> - Jurisdiction-aware from Day 0. ISO 3166-2 codes (CA-ON, US-MI, etc.) on every location-bound record.
> - Jurisdictional adapters in src/ingest/jurisdictions/<code>/. Global adapters in src/ingest/global/.
> - Agent talks to services in src/services/, never directly to ingest modules.
> - Every external API call goes through cache. No exceptions.
> - Pydantic models in src/models/. Never inline schemas.
> 
> ## Ethical rules (HARD)
> - NEVER scrape Instagram, Facebook, TikTok, or any social platform requiring auth
> - NEVER scrape FishBrain, FishAngler, or other paid fishing apps
> - NEVER build features that de-anonymize strangers' fishing locations from their content
> - NEVER export or publish spot-discovery output beyond personal use
> - Respect explicit "don't share this spot" markers in ingested content
> - Indigenous waters: flag as separate jurisdiction, do not predict within them
> 
> ## Conventions
> - uv add for deps (never bare pip)
> - ruff for lint, ruff format for formatting
> - pytest for tests, fixtures in tests/fixtures/
> - Conventional commits (feat, fix, refactor, docs, test, chore)
> - All new pydantic models get tests
> - All new external integrations get cached and have a recorded fixture for tests
> 
> ## How to run
> - make run — start CLI bot
> - make ingest — run all enabled ingestion adapters
> - make test, make lint, make format
> 
> ## Where things live
> - Product spec: docs/planning/ (read these before suggesting features)
> - System prompt: prompts/system.md (edit freely, no code change needed)
> - This file: my standing instructions, always read at session start
> 
> Every line should be something you'd consult while coding. No fluff.

Review what it produces. Edit by hand where needed — this file is the most important one in the project.

#### Step 0.4 — First commit

> Stage everything and create the initial commit. Conventional commit message. Verify .env is not staged. Then verify all the directories exist and `make test` runs (it should pass with zero tests).

Phase 0 done. You have a clean, opinionated project skeleton.

---

### Phase 1 — MVP Bot with Trip Log

Stay in the session if it's still focused, or `/clear` and re-orient briefly.

#### Step 1.1 — Plan Phase 1

Plan mode. Paste:

> Implement Phase 1: MVP fishing bot.
> 
> Components:
> 
> 1. **Jurisdiction registry** (src/jurisdictions/):
>    - Follow the [jurisdiction checklist](../../src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md) for the current registration contract.
> 
> 2. **Pydantic models** (src/models/):
>    - UserProfile: home_jurisdiction (ISO code), home_location (lat/lng/name), frequented_jurisdictions, target_species, gear (list of dicts), annual_budget, skill_level, fishing_style, preferences (free text)
>    - Trip: id, date, jurisdiction, location_name, lat, lng, species_caught, conditions, gear_used, notes, what_worked, what_didnt
>    - LureRecommendation, Observation, etc. — stubs for later
> 
> 3. **Storage** (src/storage/):
>    - profile.py: load/save user profile to data/user_profile.json
>    - trips.py: SQLite via sqlite-utils, CRUD for trips
>    - Auto-create DB and tables on first use
> 
> 4. **Agent** (src/agent/):
>    - client.py: Anthropic SDK wrapper, claude-sonnet-4-6 default, model override via env var
>    - Builds system prompt by combining prompts/system.md + user profile + recent trips + active jurisdiction context
>    - Streaming responses with rich
> 
> 5. **CLI** (src/cli/):
>    - typer-based, entry point in src/cli/main.py
>    - Commands: chat, log, recent, profile (view/edit), config
>    - `chat` is the main loop, handles tool use response cycle (no tools yet but structure for them)
>    - `log` is interactive: asks one question at a time, populates a Trip, saves
> 
> 6. **Prompts** (prompts/system.md):
>    - The bot is a fishing buddy, not a Wikipedia
>    - Always identify which jurisdiction the user's question concerns
>    - Load that jurisdiction's context; if unpopulated, say so explicitly
>    - Ground recommendations in logged trip history when available
>    - Ask one clarifying question before recommending when info is thin
>    - Concise, opinionated, specific
> 
> 7. **Tests:**
>    - Storage layer (profile load/save, trips CRUD)
>    - Jurisdiction registry (lookup, fallback)
>    - Pydantic model validation
>    - System prompt assembly (golden test)
> 
> Verification:
> - `make test` passes
> - `make run` starts the chat; can have a conversation
> - `/log` (or `log` subcommand) walks through trip entry
> - `recent` shows logged trips
> - Bot correctly identifies Ontario context for "where can I find smallmouth in the Credit River"
> - Bot correctly applies the fallback disclaimer for "what about Vermont fishing"
> 
> Write the plan as a file checklist first. Don't implement until I approve.

Review the plan checklist. Common things to push back on:
- "Schema design that conflates Trip with Observation — keep separate"
- "Skip the migrations library, sqlite-utils handles schema directly"
- "Don't add a separate config module yet; .env is fine for Phase 1"

When the plan looks right: proceed.

#### Step 1.2 — Run and use

```bash
make run
```

Test conversations:
- "Hi, what do you know about me?" — should reflect your profile
- "Recommend a lure for smallmouth in stained water around 65°F" — should give specific recommendations
- `log` — walk through logging a fake trip
- "What did I catch last time out?" — should pull from the log
- "What about fishing in Wisconsin?" — should explain it's a fallback jurisdiction

If the bot feels generic, iterate `prompts/system.md`. Tell Claude Code what's wrong and have it rewrite the prompt.

#### Step 1.3 — Commit

```
feat(phase-1): MVP fishing bot with jurisdiction-aware trip log
```

Phase 1 done. The bot is now genuinely useful for personalized fishing conversation.

---

### Phase 2 — Global Data Foundations

#### Step 2.1 — iNaturalist ingestion

`/clear` if context is cluttered, then plan mode:

> Implement Phase 2 part 1: iNaturalist ingestion.
> 
> Location: src/ingest/global/inaturalist.py — global because iNaturalist works anywhere
> 
> Requirements:
> - Async httpx client, polite rate limiting (1 req/sec)
> - Function: `fetch_observations(bbox, taxa=['Actinopterygii'], days_back=30)`
> - Returns list of Observation pydantic models (define in src/models/observation.py)
> - Cache responses in data/cache/inaturalist/ keyed by query hash, 24hr TTL
> - Persist to observations table in fishing.db with columns: id, source, source_id, species_taxon_id, species_common_name, species_scientific_name, jurisdiction, lat, lng, observed_on, quality_grade, photo_url, raw_json
> - Compute jurisdiction from lat/lng via reverse-geocode (use a shapefile or simple bounding box check for now — accuracy can improve later)
> - CLI command: `python -m src.cli.main ingest inaturalist` with options for bbox, defaults to user home + 50km
> - Tests with httpx mock; no real API hits in tests
> 
> Verification:
> - `make ingest` runs the command and populates the table
> - Query the table directly: top 10 species near home in last 6 months
> - All tests pass
> 
> Plan first.

Execute. Then run `make ingest` and verify with the bot:

> Query the observations table and show me the top 10 species observed within 25km of my home in the last 6 months. Then write me a one-paragraph summary of what this suggests about the local fishery.

#### Step 2.2 — Wire iNaturalist as an agent tool

> Now wire iNaturalist into the chat agent as a tool.
> 
> Define a tool: `get_recent_observations(lat, lng, radius_km, days_back, species_filter?)` that queries the local observations DB (not the API directly — the API runs in the ingest step, the agent queries the cache).
> 
> Update src/agent/client.py to declare the tool and handle the tool_use response loop properly. Update prompts/system.md to mention this capability so Claude knows to use it for questions like "what's been observed near X."
> 
> Verification: run `make run` and ask "what species have been observed near Lake Simcoe in the last 30 days?" Confirm the bot calls the tool, gets real data, and answers grounded in it.

This is the moment the bot becomes useful. It now answers from real local data instead of training-data generalities.

#### Step 2.3 — Add weather and conditions

> Add weather integration as the next global adapter.
> 
> Location: src/ingest/global/weather.py
> 
> Use Open-Meteo (free, no API key). Define:
> - `get_current_conditions(lat, lng)` — temperature, pressure, wind, cloud cover, precipitation
> - `get_forecast(lat, lng, days=10)` — daily forecast
> - `get_recent_history(lat, lng, days_back=7)` — needed for pressure trend analysis
> 
> Wire as agent tools: `get_conditions(lat, lng, when='now'|'tomorrow'|'in_3_days')`.
> 
> Bonus: derive "pressure_trend" (falling/steady/rising over last 24-48hr) — this is one of the highest-signal features for fishing.
> 
> Cache responses (1hr TTL for current, 6hr for forecast).
> 
> Tests with recorded fixtures. Plan first.

Execute and verify: "What are conditions like tomorrow morning at Lake Scugog? Should I go?" — the bot should pull real forecast and give a real opinion.

#### Step 2.4 — OSM water and place data

> Add OSM ingestion as the next global adapter.
> 
> Location: src/ingest/global/osm.py
> 
> Use Overpass API. Functions:
> - `fetch_water_features(bbox)` — natural=water, waterway=stream/river, natural=lake, leisure=fishing
> - `fetch_access_features(bbox)` — boat launches, parking, trails near water
> - `fetch_public_land_tags(bbox)` — leisure=park, boundary=protected_area, etc.
> 
> Persist to water_features and access_features tables.
> 
> Rate limit politely (1 req/2sec for Overpass).
> Cache aggressively (water doesn't move; 30-day TTL).
> 
> Plan first.

Now you have a baseline map of every known water body, access point, and public land tag in your region. This is the foundation for Phase 6.

#### Step 2.5 — Commit Phase 2

```
feat(phase-2): global data foundations — iNaturalist, weather, OSM ingestion
```

The bot now answers questions grounded in three independent real-world data streams.

---

### Phase 3 — Tactical Help-Buddy

The daily-use feature.

> Implement Phase 3: tactical lure/bait/technique recommendation engine.
> 
> Location: src/services/tactical.py
> 
> Functions:
> - `recommend_lures(species, conditions, water_type, time_of_day, season) -> List[LureRecommendation]`
> - `recommend_colors(water_clarity, sun_conditions) -> List[ColorRecommendation]`
> - `recommend_presentation(species, water_temp, depth) -> PresentationAdvice`
> - `compute_lure_dive_depth(lure_type, line_weight, line_type, retrieve_speed) -> float`
> 
> Implementation: rule-based first, no ML. Encode well-known patterns:
> - Cold water → slow, suspending, jerkbait/suspending crankbait
> - Warm clear → natural colors, finesse
> - Stained → chartreuse, orange, vibration (spinnerbait, lipless)
> - Bass spawn → flipping jigs to beds (with ethical caveat to release immediately)
> - etc.
> 
> Add as agent tool: `get_tactical_recommendation(species, conditions_summary, ...)` — bot can call this proactively when user asks "what should I throw."
> 
> Store recommendations as structured data so we can later track which ones worked (via trip log feedback).
> 
> Pydantic model LureRecommendation: type, brand_examples (list, optional), color, size, weight, depth_range, retrieve_speed, when_to_use.
> 
> Tests with parametrized cases.
> 
> Plan first.

Execute. Test:
> "I'm fishing the Credit River tomorrow morning, water is stained from rain, air temp 12°C, going for smallmouth. What should I throw?"

A good response cites the conditions, names 2-3 specific lure choices, explains why, and notes anything risky (water temp is marginal for smallmouth aggression).

Commit Phase 3.

---

### Phase 4 — Government & Regulatory Data

Start with Ontario as the reference implementation.

#### Step 4.1 — Regulations ingestion (Ontario first)

> Implement the Ontario regulatory adapter.
> 
> Location: src/ingest/jurisdictions/ca_on/
> 
> Files:
> - regulations.py: parse the MNRF Fishing Regulations Summary PDFs (one per zone). Use pypdf or pdfplumber for text extraction. Output structured: zone, species, open_season, size_limits, daily_limit, possession_limit, special_rules.
> - stocking.py: download Ontario fish stocking data CSV from Ontario open data portal. Parse and persist to stocking table.
> - boat_launches.py: MNRF boat launch dataset.
> 
> All persist to jurisdiction-tagged tables.
> 
> Set Ontario’s plugin `regulatory_context` for its prompt guidance; see the [jurisdiction checklist](../../src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md).
> 
> Add agent tools:
> - `get_regulations(species, lat, lng)` — returns rules for species at location
> - `get_stocking_history(waterbody_name)` — what's been stocked and when
> 
> Verification: ask the bot "can I keep a 14-inch walleye in Lake Simcoe in May?" — should pull real Zone 16 regulations and answer correctly.

Run and verify. Some regulations PDFs are messy; expect to iterate on the parser.

#### Step 4.2 — Generalize for adding more jurisdictions

> Now that Ontario is real, refactor for adding other jurisdictions.
> 
> Follow the [jurisdiction checklist](../../src/ingest/jurisdictions/JURISDICTION_TEMPLATE.md) when adding adapters.
> 
> Implement a Michigan adapter as the second example (Michigan DNR has good open data — fishing regs PDFs, stocking CSVs, public access sites). Same structure as Ontario.
> 
> Add Michigan’s plugin following that checklist.
> 
> Don't implement other states/provinces yet — but ensure the registry knows about them with the fallback so the bot doesn't crash.

Now you have two real jurisdictions and a pattern for adding more.

Commit Phase 4.

---

### Phase 5 — Hydrological Network Analysis

This is the big differentiator. Multi-week phase.

> Implement Phase 5: hydrological stream network analysis. Reference docs/planning/fishing_ai_advanced_capabilities.md Section 1.
> 
> Scope: build the system for Ontario first (using OHN data); architecture must accept other jurisdiction inputs later (NHD for US, etc.).
> 
> Steps:
> 
> 1. Data ingestion: load Ontario Hydro Network from Ontario GeoHub into PostGIS-equivalent SQLite (use SpatiaLite). Persist as stream_segments table with geometry, gradient (computed from DEM), watershed_area, flow_direction.
> 
> 2. Add Ontario barrier dataset (dams, falls, culverts) to barriers table.
> 
> 3. Build the graph: src/services/hydrology.py. Use networkx. Each stream segment is an edge, junctions are nodes. Add barriers as edge attributes (passable/impassable per species, since brook trout can't pass what bass can).
> 
> 4. Seed with known data: tag segments where iNaturalist/MNRF surveys/your trip log show confirmed catches.
> 
> 5. Implement core queries:
>    - `upstream_of(point) -> List[Segment]`
>    - `downstream_of(point) -> List[Segment]`
>    - `reachable_from(point, species) -> List[Segment]` (respects species-specific barriers)
>    - `connected_tributaries(main_stem) -> List[Tributary]`
> 
> 6. Add agent tools: `analyze_watershed(waterbody_name)`, `find_connected_tributaries(waterbody_name, species)`.
> 
> Verification:
> - Load full 16 Mile Creek watershed
> - Query "what tributaries of Sixteen Mile Creek are accessible to brook trout from the headwaters" — returns real list
> - Render a quick test map in QGIS or with folium to visually verify the graph
> 
> Plan in detail. This is multi-session work — break into sub-plans.

This takes time. Expect multiple Claude Code sessions. Commit frequently.

Phase 5 outcome: when you ask the bot about a watershed, it pulls real hydrological structure and species reachability. No other consumer fishing tool does this.

---

### Phase 6 — Spot Discovery from Satellite + Accessibility

> Implement Phase 6. Reference docs/planning/fishing_ai_spot_discovery.md.
> 
> Multi-part. Plan the breakdown first; we'll execute in sub-phases.
> 
> Sub-phases:
> 
> 6a. Water body inventory: union of OSM water + JRC Global Surface Water + Dynamic World for your home region. Persist as water_bodies table with: id, geometry, jurisdiction, source_list, area_m2, classified_type (initially null).
> 
> 6b. Microsoft Building Footprints ingestion. Persist as buildings table. Compute proximity-to-water for each water_body.
> 
> 6c. Public land overlays: Ontario Crown Land Atlas, Conservation Authority boundaries, Provincial Parks. Persist as public_land table. For US jurisdictions, add BLM/USFS/NPS layers when implemented.
> 
> 6d. Accessibility scoring service: src/services/accessibility.py. Implement the scoring function from spot_discovery.md Section 4. Outputs 0-100 score with enumerated contributing factors. Critical: the score has hard exclusions (building within 50m) that drop to near-zero regardless of positive signals.
> 
> 6e. Classifier: rule-based decision tree to label water bodies as creek/pond/lake/quarry/stormwater/reservoir. Use shape metrics, OSM tags, nearby building patterns, satellite imagery characteristics where needed.
> 
> 6f. Higher-resolution discovery for missed water bodies: SAM or simpler thresholding on SWOOP imagery (Ontario) / NAIP (US) to find small water bodies not in coarser datasets. Optional for v1 — get the basic pipeline working first.
> 
> Plan 6a–6c as one sprint, 6d–6e as a second sprint, 6f as a stretch.
> 
> Verification at end of 6a-6e: query "find me water bodies within 30km of home, accessibility score > 50, not currently in my trip log." Returns real list.

Multi-session, multi-week. The payoff is huge — this is where the bot starts surfacing actual exploration candidates.

Critical reminder during this phase: keep the output personal. Do not export, share, or publish spot lists. This is for your eyes only.

---

### Phase 7 — Habitat-Based Species Prediction

> Implement Phase 7. Reference docs/planning/fishing_ai_advanced_capabilities.md Section 5.
> 
> Now ties everything together: hydrology graph + water bodies + observations + government surveys + your trip log → species probability per water body.
> 
> 1. Feature engineering: for each water_body or stream_segment, compute features:
>    - Physical: gradient, area, watershed_area, elevation, depth_estimate (where bathymetry available)
>    - Climate: mean annual temp, summer max, modeled water temp
>    - Land cover: % forest, % agriculture, % urban in watershed (NLCD or SOLRIS depending on jurisdiction)
>    - Connectivity: distance to barriers, network position
>    - Biological: confirmed presence of competitors/prey, stocking history
> 
> 2. Training data: known presence/absence from iNaturalist + MNRF surveys + your trip log. Per species.
> 
> 3. Models: random forest classifier per species (scikit-learn). One per (species, jurisdiction) pair to start.
> 
> 4. Inference: for any unsurveyed water body, predict probability of presence for each candidate species.
> 
> 5. "Untapped potential" composite:
>    untapped = predicted_presence × (1 / report_density) × access_score
> 
> 6. Add agent tool: `predict_species(lat, lng) -> List[(species, probability, confidence, top_features)]`
> 
> 7. Add CLI command: `python -m src.cli.main explore --species brook_trout --radius 50` — returns top 20 untapped candidates with map links.
> 
> Verification: pick a known brook trout stream you've fished; the model should give it high probability. Pick a known bass lake; brook trout probability should be low. Sanity check before trusting outputs for novel locations.
> 
> Plan first.

This is where the bot stops being a fishing assistant and starts being a research platform.

---

### Phase 8 — Map UI + Voice + Offline

> Implement Phase 8: the map UI.
> 
> Stack: FastAPI backend exposing read APIs over the existing SQLite DB. Next.js + Leaflet + TailwindCSS frontend.
> 
> Backend (src/api/):
> - /api/water_bodies?bbox=...&filters=...
> - /api/observations?bbox=...
> - /api/trips
> - /api/predict?lat=...&lng=...
> - /api/chat (streaming response from the agent)
> 
> Frontend (separate ui/ directory, Next.js):
> - Map view: Leaflet with multiple layers (water bodies colored by access score, observations clustered, trip log pins, prediction heatmap toggle)
> - Side panel: clicking any feature pulls details
> - Chat panel: integrated with the agent, with selected-feature context passed in
> - Trip log view: timeline + map
> - Discovery view: ranked candidate list with filters
> 
> Critical UI decisions:
> - Mobile-first responsive (you'll use this on phone in the field)
> - Offline mode: pre-download tiles + data for planned trips
> - Voice input button for hands-free use
> 
> Plan in detail — this is multi-week. Sub-plan the backend separately from the frontend.

This is where the project becomes something you'd show someone (carefully — remembering the personal-use ethics).

---

## Part V — Vibe Coding Discipline

The tactical habits that make all of the above actually work.

### Session hygiene

- **One task per session.** Don't combine "add weather" with "fix the chat bug." Context bleed produces bad output. `/clear` between unrelated tasks.
- **Plan mode for anything non-trivial.** `Shift+Tab` twice. The cost of planning is near zero; the cost of wrong code is hours.
- **`/compact` mid-task** when context fills. Claude tells you when it's getting tight.
- **Commit constantly.** After every working unit. Git is your undo button when Claude messes up.

### Verification is non-negotiable

Claude will say "tests pass" without running them. End every prompt with how to verify:
- A test command (`make test`)
- An expected output ("the bot should respond with X")
- A specific query result ("the observations table should have rows")

If Claude claims done without evidence, ask for the evidence.

### Model selection

| Task | Model |
|---|---|
| Architecture decisions, hard problems | Opus (sparingly) |
| Most coding | Sonnet (default) |
| Bulk parallel work (e.g., parsing 50 PDFs) | Haiku via subagents |

Switch with `/model opus` etc. Don't run Opus on everything — it's slow and expensive for routine work.

### CLAUDE.md is your project's brain

- Every time you correct the same mistake twice, add a line.
- Every time conventions evolve, update it.
- Keep under 200 lines. Split via `@imports` if it grows.
- Every line earns its place — would removing it cause Claude to make a mistake? If no, cut it.

### When things go wrong

- **`/rewind`** — undo recent tool calls if Claude just made a mess.
- **Stop mid-stream** — don't let bad code accumulate. Interrupt and redirect.
- **`/clear` + brief** — sometimes fresh context is faster than fighting an entrenched assumption.
- **Stricter verification** — if Claude keeps claiming things work that don't, demand output before accepting "done."

### Cost awareness

- `/cost` shows session spend. Personal-use phases run $1-5/session typically.
- Cost spikes usually mean: forgetting to `/clear` between tasks, or using Opus when Sonnet would do.

---

## Part VI — The Order of Doing

For when you don't want to read this whole doc:

**Today (Day 1):**
1. Install Claude Code, Python, uv, git
2. Create `fishing-bot/`, copy planning docs in
3. Launch `claude`, run the orientation prompt
4. Phase 0: scaffold (plan mode)
5. Phase 0: CLAUDE.md
6. First commit

**This week:**
7. Phase 1: MVP bot + trip log
8. Phase 2.1: iNaturalist
9. Phase 2.2: wire as tool
10. Use it. Log every trip.

**Next 2 weeks:**
11. Phase 2.3-2.4: weather + OSM
12. Phase 3: tactical recommender
13. Use it. Log every trip.

**Month 2:**
14. Phase 4: Ontario regulations
15. Phase 5 part 1: hydrology data ingestion + graph

**Month 3:**
16. Phase 5 part 2: queries + tools
17. Phase 6 part 1: water body inventory + buildings + public land

**Month 4:**
18. Phase 6 part 2: accessibility + classifier
19. Phase 7: species prediction model
20. Phase 8: map UI begins

**Ongoing forever:**
- Log every trip
- Update CLAUDE.md
- Add jurisdictions as you travel
- Refine prompts
- Retrain models on new data

---

## Part VII — The One Thing

If you take only one thing from this entire document: **build the trip log well, fill it religiously, and protect that data**. Every other feature gets dramatically better with months of structured personal data behind it. Without it, you have a fishing app. With it, you have something no one else can replicate.

Tight lines.
