## Project

Personal fishing exploration bot. Multi-jurisdiction (Canada + US), Ontario first. **Phase 1 complete** — all data ingestion layers built and verified. Gamification, aquarium, trip planner, map UI all deferred. NOT for public release.

## My fishing context

- Home: Oakville, Ontario (CA-ON)
- Target species: ALL species including microfishing targets (darters, dace, madtoms, shiners, chubs, lampreys) — not just popular gamefish
- Fishing style: stream + small lakes primarily
- Skill: intermediate
- Top priority: exploration over catch optimization
- First-time vibe coder — explain things plainly, no jargon assumed

## Tech stack

Python 3.11+, uv, SQLite via sqlite-utils, Anthropic SDK (claude-sonnet-4-6 default), typer CLI, pytest, ruff.

## Architectural rules (enforced)

- Every location-bound record carries ISO 3166-2 jurisdiction code (CA-ON, US-MI, etc.)
- Global ingest adapters: `src/ingest/global/` — work anywhere
- Jurisdiction-specific adapters: `src/ingest/jurisdictions/<code>/` — Ontario built first, others added as adapters
- Agent talks to services (`src/services/`), never directly to ingest modules
- Every external API call goes through cache. No exceptions.
- Pydantic models live in `src/models/` — never inline schemas
- **Everything that reaches a model goes through the context layer** (`src/services/context/`). No service builds its own prompt block from raw rows. That is how `ontario_species_ranges.json` reached the agent as retrieved fact and how `coaching.py` ended up with no SAR check at all.
- **`src/services/context/render.py` is the only place context becomes text.** Per-call-site formatting is the prompt-drift problem one layer down, in as many copies as there are call sites.

## The context layer

Three entry points in `src/services/context/__init__.py`:

- `describe(place, caller=...)` — one stretch of water, sliced. Callers do not pick slices; `_BUNDLES` maps caller type to slices, and `_ESCALATING_CALLERS` decides who may spend a live web search when the corpus is empty.
- `explore(area)` — ranks unvisited segments. No habitat term, no species prediction.
- `user_layer(user)` / `species_history(db, species)` — derived, never raw rows.

**The derived layer is computed on write.** `log_session` calls `recompute_user_layer`; `user_layer()` serves the stored result whenever the inputs are unchanged. Freshness is a **fingerprint** of the inputs (row counts + max ids for stops, sessions and current insights), not an age — a stale row is caught by the data having moved, so a direct DB edit cannot serve a wrong answer forever. A miss recomputes: the cache is an optimisation and never a source of truth, and dropping `user_patterns` costs time, not answers.

The freshness check is deliberately one query. The first version issued six and was *slower* than deriving for any log under ~300 stops — a cache whose freshness check costs more than a miss is not a cache. Measured after the fix: 1.2× at zero stops, 3.7× at 100, 19× at 1,200.

Registry exports for `verify-species-status` live in `data/registry/`. The command defaults to `data/registry/cosewic_2024.csv` and reads its citation from the file's own `source`/`source_url` columns — retyping a citation on the command line is how a run stamps the wrong one onto a status.

Every value is a `ContextField`: value + `Provenance` (RECORD / WEB / INFERENCE) + `EmptyReason`. `WEB` is forced `verified=False` by a model validator. There are eight empty reasons and they are not interchangeable — each one has a different remedy for the reader.

**`sar_alert` vs `status_known_listed`.** All 69 species in the local file have unverified conservation status, so `sar_alert` is `True` for every one of them — correct for suppressing the corpus's own generated angling text, useless as a refusal gate. Gate hard refusals on `status_known_listed` (an affirmative listing signal from some authority, verified or not). A rule that fires on every fish in Ontario protects nothing.

## Agent tool surface

14 tools in `src/agent/tools.py`, down from 32. `chat.py` delegates; it holds the loop, not the tools.

`describe_place` is the primary tool and absorbed fourteen per-dataset lookups. `get_conditions` stays separate because it is the only live call — the split is by cache policy, not by preference. `get_tactical_recommendation` and `src/services/tactical.py` were deleted: unsourced gear advice handed to the model as a tool result is indistinguishable from a record, which is the same defect as the #16-hook entry in the species corpus.

When adding a dataset, wire it into a slice. Do not add a tool.

## Ethical rules (decided, do not relitigate)

- Synthesizing public information is FINE: named locations in public videos, forum posts, iNaturalist observations, government datasets, YouTube transcripts
- Reconstructing deliberately-hidden information is NOT: no vision pipeline to de-anonymize locations a creator intentionally obscured
- No scraping: Instagram, Facebook, TikTok, FishBrain, FishAngler (ToS + active enforcement)
- Indigenous/First Nations waters: flag as separate jurisdiction, do not predict within them
- Spot discovery output stays personal — no export features that broadcast spot lists

## Data quality principle: obscured iNaturalist observations

Obscured iNaturalist observations are not discarded and not treated as precise. They contribute soft presence evidence distributed across stream segments within the obscuration radius (22 km), weighted by habitat suitability. This preserves the conservation value of the geoprivacy feature while extracting maximum signal from the data.

The `observations` table stores three fields to support this: `geoprivacy` ("open"/"obscured"/"private"), `is_obscured` (bool), and `obscuration_radius_km` (22.0 for obscured, None for open). The soft-label pipeline in the SDM feature matrix uses `get_obscured_observations()` from `src/storage/observations.py` to retrieve these records for distributing presence weights across candidate segments.

## Data quality principle: salmonid stocking confound

Stocked salmonids are planted at accessible put-and-take sites selected for logistics (road access, parking, stocking truck routes), not habitat suitability. Training on those presences teaches the model "where MNRF trucks can reach", not "what habitat supports this species". The `is_stocked_within_5yr` flag in the SDM feature matrix is used to exclude stocked-site records during training — but **only for the species where this bias is material**.

Species requiring stocking exclusion (`STOCKING_CONFOUND_SPECIES` in `src/services/sdm_training.py`):
- **Oncorhynchus mykiss** (Rainbow Trout) — most severe: 3.67M fish, 187 ON sites, 2021–2025
- **Salvelinus fontinalis** (Brook Trout)
- **Salmo trutta** (Brown Trout)
- **Oncorhynchus tshawytscha** (Chinook Salmon)
- **Oncorhynchus kisutch** (Coho Salmon)
- **Salvelinus namaycush** (Lake Trout)

Non-salmonid species (Creek Chub, Yellow Perch, Rainbow Darter, etc.) are never stocked at scale in Ontario. Applying stocking exclusion to them incorrectly removes valid habitat observations at stocked sites (which are also real stream habitat). The `stocking_exclusion` parameter in `prepare_species_data()` is ignored for species outside this list.

## The SDM is dormant, not a prediction layer (decided, do not relitigate)

Measured 2026-10-05 with spatial block CV, after fixing that CV so an
unevaluable run no longer reports 0.5 (see `SpatialCVResult`). Fourteen species,
all near chance:

| Narrow-niche specialists | pres | AUC | Generalists | pres | AUC |
|---|---|---|---|---|---|
| Iowa darter | 42 | 0.6018 | Creek chub | 459 | 0.6156 |
| Least darter | 50 | 0.3361 | White sucker | 378 | 0.6157 |
| Fantail darter | 128 | 0.3881 | Largemouth bass | 309 | 0.5667 |
| Central mudminnow | 103 | 0.5591 | Rock bass | 312 | 0.5111 |
| Stonecat | 129 | 0.4135 | | | |
| **mean** | | **0.4597** | **mean** | | **0.5773** |

The nine production species in `SPECIES_TO_TRAIN` span 0.41–0.62, mean ≈ 0.57.
Best case anywhere is 0.62.

**Why, and why more features will not fix it.** Presence is near-universal
within the obvious habitat type — any Ontario creek probably holds creek chub,
any lake holds some bass — so there is little for a habitat model to separate.
The records are presence-only, so `generate_pseudo_absences` has to invent the
negatives, and it draws them from the same observer-effort distribution as the
presences. That is the presence-vs-pressure thesis applied to the model's own
training data.

**The niche hypothesis was tested and does not hold.** The intuition is that
SDM should work for narrow-niche rarities (Iowa darter, least darter) even if
it fails for generalists. Measured, specialists score *worse* — three of five
below chance. Two mechanical reasons: rare species have few, spatially
clustered records, so spatial blocking cannot form folds (least darter ran 2/4,
stonecat and fantail 3/4); and the feature set is reach-scale (stream order,
substrate category, mean temperature) while a darter's niche is vegetation and
substrate at centimetre scale. The features cannot see the niche. This is "not
with this data volume and this feature set", not a refutation of the ecology.

**What this means for the product.** A model output will never be more credible
than a record of someone catching a fish at that spot. The bot's job is
retrieval and explanation, not prediction: say what is known, where it came
from, and why the water looks the way it does. `Provenance` already ranks
RECORD / WEB / INFERENCE; the survey fields on `gbif_observations`
(`sampling_protocol`, `event_id`) extend that *inside* RECORD — a standardised
electrofishing haul with known gear outranks a casual photo, and both outrank
anything a model emits.

**Status.** The pipeline stays — it is tested and the measurement is cheap to
re-run. `sdm_predictions` stays empty and nothing in the context layer reads
it. Re-open only if the inputs change in kind, not in volume: true absences
from survey effort (see `event_id` grouping), or microhabitat-scale features.
Do not re-open to add presence records or tune the feature list; that was tried.

`length_m` was removed from `_NUMERIC_FEATURES` on principle (digitization
artifact, was ranking 2nd at ~0.20 importance), **not** for accuracy: across
nine species it is a wash, mean +0.0035, with two species meaningfully worse.

## Phase 2d: Untapped potential — access score coverage limitation

Access scores (`src/services/accessibility.py`) are only meaningful within the OSM ingestion radius (~55km of home). The OHN stream network covers all of Ontario (309k segments), but access point data (roads, parking, buildings) is fetched for 25km around home. Segments outside this radius receive a neutral baseline score (~0.27 after normalization) and are not meaningfully differentiated by access.

`explore()` results are most reliable within the home-area radius. Beyond 55km the score is dominated by observation pressure, structure and remoteness — access adds no signal there and says so.

**This is now a data field, not just a caveat.** `compute_access_scores` records `access_is_measured` per segment (derived from the road modifier, which already knew — it gives out-of-footprint segments a neutral value for exactly this reason and used to throw the distinction away). `ExploreResult.access_is_measured` and `ExploreResponse.results_on_placeholder_access` carry it to the surface, and the renderer prints "access not measured here" instead of a number. Nothing is filtered: pressure, structure and remoteness are real outside the footprint, so the results stand — only the access term does not.

The field is **tri-state**, not boolean: `True` measured, `False` known to be outside the footprint, `None` unclassifiable because the cached scores predate coverage tracking. Those are three facts with three remedies, and the renderer prints each differently — collapsing `None` into `False` would invent a claim about remoteness out of a stale parquet. An access parquet written before this column returns `None` from `load_cached_coverage()`; `make compute-access` settles it.

## Known issues

- **`stream_order` is unpopulated on all 20,339 OHN segments.** The adapter ingests the segments but captures no stream order, so `describe()` reports `FIELD_NOT_POPULATED_BY_SOURCE` for it — correctly, but the field is unusable until the adapter is fixed. `explore()` is unaffected: it reads `stream_order` from the feature-matrix parquet, which does have it.
- **BC NuSEDS and QC species-ranges discovery filters use brittle label matching**, the same failure class as the PWQMN bug (matching an exact published label rather than structure). `ca_bc/nuseds.py` anchors on `name.startswith("all areas nuseds")`; `ca_qc/species_ranges.py` matches a fixed keyword set. Both are in frozen jurisdictions and not yet fixed; a rename upstream would silently yield zero records. `src/ingest/discovery.py::check_resource_discovery` exists to make that loud — neither adapter calls it yet.

### Silent diagnostics — the recurring failure class

The dominant bug shape in this codebase is a function that computes the number distinguishing success from silent failure, then discards it. Four instances were found and fixed (PWQMN discovery, CLI log configuration, the MNRF FMZ field-name parse, the `verify-species-status` name join). These remain, in rough priority order:

- **`ebird.py:134,139,146` and `geology.py:109` skip rows silently.** eBird drops observations on unparseable dates and IDs; the geology KML parser drops malformed coordinate pairs, so a polygon can quietly lose vertices and still parse. Neither counts what it dropped.
- **`sdm_features.py::coverage_fraction` is printed by `build-features` and never stored.** Coverage degrading run-over-run is invisible because nothing compares against the last value.
- **`ca_ab/stocking.py:192` and `ca_bc/nuseds.py:195` log `n_skipped` at INFO**, so it is invisible without `-v`. `ca_on/water_quality.py:362` logs the same class of count at WARNING and is the pattern to copy. Both INFO cases are in frozen jurisdictions.
- **`synthesis_cache.py:236,249,263` swallow cache-write failures.** A permanently failing cache is indistinguishable from a cold one; the only symptom is the API bill.
- **`chat.py:150,542` swallow `tool_usage` insert failures.** Telemetry only, but a broken table reads as an agent that never calls tools.

The rule when touching any of these: a count that separates "worked" from "silently did nothing" belongs at WARNING when the share is material, and in the return value always.

## Data quality principle: culverted urban streams

Small order-1 and order-2 streams in high-density urban areas are frequently culverted in southern Ontario. OHN maps these hydrologically but they may not be fishable on the ground. Default `min_stream_order=3` in `explore()` excludes most culverted reaches.

The `exclude_likely_culverted` heuristic (order-1/2 segments with `observation_density_25km > 100`) went with `find_untapped_water_for_agent` and has no replacement. It was a reasonable guess, not a measurement, and nothing in `explore()` applies it today — so the stream-order default is the only culvert defence currently in place.

TRCA-managed streams with SAR habitat designations are exceptions — they may be legitimate targets even if hard to find on consumer maps. Eckardt Creek (Rouge tributary, Markham) is a documented example: actively managed for Redside Dace, partially channelized but fishable via the Rouge River trail system.

## Core principle: presence vs. pressure

Crowdsourced catch and observation data measures angler activity as much as fish presence. The bot must not confuse the two:

- High report density does not imply high habitat quality. It often implies high pressure.
- Low report density does not imply absence. It often implies low access or low observer effort.
- Habitat features and systematic survey data (Conservation Authority electrofishing surveys, government datasets) are stronger signals than catch reports for predicting where fish actually live.
- "Untapped potential" inverts report density: high habitat × low reports × good access = top score.
- When citing community data, the bot should distinguish between "fish are here" (presence) and "people are here" (pressure).

Refinement: Some famous spots — Caledonia for walleye/gar, Dunnville for channel cats, the Thames for redhorse — are popular because of structural productivity (chokepoints, spawning runs, rare habitat) that pressure cannot fully erase. The bot tracks reputation, pressure estimate, and structural productivity as separate signals. A spot can be high on all three; the bot acknowledges this honestly. The user's question determines whether reputation/pressure are weighted as positive (they want a sure bet) or negative (they want solitude). Never collapse these into a single score.

This is the project's central thesis. It reshapes every prediction the bot makes.

OSM data tells us what water exists and where. It does not tell us whether fish are there or in what quantity. Never use water body size, name presence, or access quality as proxies for fish abundance or quality. These are convenience factors only. Habitat suitability and species predictions require Phase 2 data layers. When asked for "best spots", always be explicit about what data is and isn't available yet and what is being built to fill the gap.

Water quality parameters (dissolved oxygen, pH, temperature) are a separate category: they are direct habitat constraints, not presence indicators. If DO is below a species' tolerance floor or pH is outside its viable range, the species cannot be there regardless of what crowdsourced data says. Use 1o/1s data to rule out implausible predictions; never use it to confirm presence. A site that passes water quality thresholds is merely habitable — not confirmed occupied.

## Conventions

- `uv add` for all dependencies (never bare pip)
- ruff for lint + format
- pytest for tests, fixtures in `tests/fixtures/`
- Conventional commits: feat, fix, refactor, docs, test, chore
- All new pydantic models get tests
- All new external API integrations get cached + have a recorded fixture for tests
- Assistant content blocks sent back to the API must be serialized to only API-accepted fields — never use `.model_dump()` directly on SDK content blocks because it includes internal fields the API rejects
- Never insert test or seed data into the production database during development. Tests must use temporary databases (e.g. pytest's `tmp_path`) to avoid polluting real user data.

## How to run

- `make run` — start CLI bot
- `make ingest` — run ingestion adapters
- `make test` — run tests
- `make lint` — check style
- `make format` — auto-format

## Where things live

- Product spec: `docs/planning/` (read before suggesting any feature)
- System prompt: `prompts/system.md` (edit without code changes)
- This file: read every session start

## Phase 1 data source roadmap — COMPLETE

All sub-phases committed as of 2026-05-26. Phase 2 planning begins next session.

### What was built

| Sub-phase | Description | Adapter | Rows |
|-----------|-------------|---------|------|
| 1a | Project skeleton | — | — |
| 1b | MVP chat bot, profile, trip log, jurisdiction registry | — | — |
| 1c | iNaturalist ingestion + agent tool | `global/inaturalist.py` | 284 observations |
| 1d | Open-Meteo weather + barometric pressure trends | `global/weather.py` | live |
| 1e | Tactical recommender | — | — |
| 1f | GBIF species occurrence | `global/gbif.py` | 3,501 occurrences |
| 1g | Water Survey of Canada stream gauges | `global/wsc.py` | 297 gauge readings |
| 1h | OpenStreetMap water features + access | `global/osm.py` | 25,914 water features, 23,852 access points, 35 barriers |
| 1i | MNRF stocking history | `ca_on/stocking.py` | 12,756 stocking records |
| 1j | Native range maps + Species at Risk overlays | `ca_on/species_ranges.py` | 64 species ranges |
| 1k | Reddit community RAG with technique extraction | — | (RAG index; 0 posts cached) |
| 1l | Conservation Authority fish surveys | — | BLOCKED — see note below |
| 1m | Ontario Hydro Network + stream connectivity graph | `ca_on/hydro_network.py` | 28,473 stream segments |
| 1n | MNRF regulations parser | `ca_on/regulations.py` | 20 FMZ regulation chunks |
| 1o | Ontario Water Quality Monitoring Network | `ca_on/water_quality.py` | 17,507 readings (pH, DO, temp, conductivity) |
| 1p | CABIN benthic macroinvertebrate data | `ca_on/benthic.py` | 3,310 samples |
| 1q | Ontario surficial geology (substrate type) | `ca_on/geology.py` | 10,751 geology units |
| 1r | eBird piscivore observations | `global/ebird.py` | 1,558 bird observations |
| 1s | DFO stream temperature network | `global/hydat_temperature.py` | 435 station summaries |

### Phase 2 (next session — begin planning)

- Satellite imagery ingestion (Sentinel-2, NAIP, SWOOP)
- Satellite-derived bathymetry
- Microsoft Building Footprints + accessibility scoring
- Spot discovery pipeline
- Habitat-based species distribution models (first real ML layer)
- Multi-jurisdiction expansion (BC, Quebec, US states beyond stubs)

## Data source reality check: 1l

**MNRF Broadscale Monitoring (BsM) fish community data is not publicly available.**
The actual survey records (species counts, lengths, weights from standardised lake
netting and electrofishing) live in an internal MNRF database called `fishnetv3`.
There is no public API, no bulk export, and no ArcGIS FeatureServer for this data.

**Fish ON-Line is UI-only.** The GeoHub item (`4ee94762ab4e453f95fd977bfbf59e4a`)
resolves to a Geocortex web application backed by a single MapServer with 13 layers —
all administrative (access points, management zones, bathymetry, licence issuers).
No species observation layer exists in the REST service. Species data shown in the app
is served by internal Geocortex workflows with no queryable external endpoint.
Bulk download is not possible; the open data catalogue entries are HTML links to the
app itself.

The only publicly available BsM data is **water chemistry** (pH, TP, DOC, 2008–2023)
on data.ontario.ca — not fish community records.

**TRCA RWMP is the closest real alternative.** The Toronto and Region Conservation
Authority publishes Regional Watershed Monitoring Program (RWMP) fish community data
at data.trca.ca — stream electrofishing (OSAP single-pass) across 9 Toronto-region
watersheds (Humber, Don, Rouge, Duffins, Carruthers, Highland, Petticoat, Etobicoke,
Mimico), 26 fixed stations resurveyed every ~3 years since 2000. Fields: species,
count, total weight. SAR records removed from public release.
Direct CSV: `data.trca.ca/dataset/00c1bab2-f6f5-44a9-9cc0-830960530f04/resource/
4cca6683-a08b-4d0c-8faf-4952fca0ef58/download/2020-rwmp-fish-community-data.csv`
**Caveat:** the data.trca.ca portal was consistently unresponsive during research
(May 2026). When the portal becomes reliably accessible, a TRCA adapter can be added
following the same pattern as `src/ingest/jurisdictions/ca_on/stocking.py`.
