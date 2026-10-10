# Fishdex

A personal fishing exploration AI assistant. Built for anglers who want to find small streams and microhabitats — including microfishing targets like darters, dace and madtoms — instead of only popular gamefish spots.

## What it does

Fishdex answers fishing questions from a local corpus of integrated public datasets, using Claude to reason over retrieved records. It runs as a CLI chat bot and as a web app — chat, map, trip log and a species collection — backed by a FastAPI service.

(The repository, Python package and CLI command are still named `fishbot`.)

## The thesis

Most fishing apps take one of two shapes. Either they rank the popular spots better than the last app did, or they hand you a model's confidence score — 87% — and ask you to trust it. Both fail the same way for the angler who wants to find water nobody has written about.

Fishdex is built on a different bet about what AI is *for* here.

**The AI does not predict where fish are. It retrieves what is actually recorded, and reasons over that.** This is not a hedge — it is the whole design. A model that outputs a suitability score is compressing a corpus of specific, checkable facts into one opaque number. That number is worse than the facts it replaced: you cannot verify it, you cannot learn from it, and you cannot tell the difference between "this water is poor" and "nobody has ever looked here".

This project tested the alternative honestly. A species distribution model was built, evaluated, and **retired** at 0.51–0.61 AUC — barely better than random. The failure was instructive: the generalist species with enough training data live nearly everywhere, and the specialists this app exists to serve never clear the threshold. The replacement is not a better model. It is no model, and a retrieval layer instead.

### What that buys the person using it

**You can check its work.** Every claim carries structured provenance — a record with its source and date, a web result marked unverified, or an inference with no source at all. These render differently, always. When the app says a species is in a creek, you can see it came from an iNaturalist observation dated last June. A score cannot do that, and the stakes are concrete: acting on a fabricated claim means a 45-minute drive to water that never held the fish.

**It tells you what it does not know, and why.** "No data" is not an acceptable answer, so it is never given. Nothing recorded within the radius; you have never fished here; the web search came back empty; this source does not cover this area; we hold records here but this field is unpopulated; the live lookup failed just now; we measured it and the number would not change your plan; we hold it and are deliberately withholding it on conservation grounds — eight different statements with eight different remedies. Knowing *which* gap you are looking at is what makes a gap actionable.

**It works for the species nobody else covers.** Because nothing depends on a per-species trained model, a darter, a madtom and a smallmouth are all first-class. The long tail is not a rounding error here; it is the point.

**It adapts to you from what you do, not what you configure.** There is no profile screen asking you to pick target species or a skill level. What you care about, how experienced you are, and what counts as a useful insight are all derived from your logged trips — including the blanks, which are half the signal and the hardest thing to get people to record. Telling an angler who logs madtoms on a tanago hook something obvious, confidently, destroys credibility. So the register is calibrated from demonstrated expertise, and claims about *your* patterns wait until there is a comparison set to support them.

**It biases toward discovery.** Rankings reward water that is under-reported, structurally interesting and reachable — not water that is already popular. Finding the places nobody ranked is the product.

### The data principle underneath

**Fish presence and angler pressure are different signals**, and collapsing them is how the ranking goes wrong. High report density usually means popular water, not abundant fish. Low density often means nobody has looked. Habitat features and systematic survey data are stronger evidence than catch reports — and water chemistry is a constraint, never a confirmation: it can rule a species out of a reach, but passing it only means the water is habitable, not occupied.

One more rule the whole layer obeys: **a value only ships with its "so what"**. Raw numbers travel with their plain-language meaning, and if a number has no honest meaning for an angler, it is not surfaced. A mid-range pH is reported as measured-but-unremarkable rather than dressed up as insight.

### Data sources integrated

| Source | What it provides | Scope |
|--------|-----------------|-------|
| [iNaturalist](https://www.inaturalist.org) | Species observations (citizen science) | Global |
| [GBIF](https://www.gbif.org) | Species occurrence records (museum + survey aggregates) | Global |
| [Open-Meteo](https://open-meteo.com) | Weather forecasts + barometric pressure trends | Global |
| [Water Survey of Canada](https://wateroffice.ec.gc.ca) | Real-time and historical stream gauge data | Canada |
| [OpenStreetMap](https://www.openstreetmap.org) | Water features, access points, parking, portage routes | Global |
| [eBird](https://ebird.org) | Piscivore observations as a biological proxy | Global |
| DFO stream temperature | Thermal regime classification by station | Canada |
| [MNRF Fish Stocking](https://www.ontario.ca/data/fish-stocking-summary) | Stocking history with species, life stage, density | Ontario |
| [MNRF/NatureServe Species Ranges](https://www.ontario.ca/page/species-risk-ontario) | Native range polygons + Species at Risk status | Ontario |
| [Ontario Hydro Network](https://geohub.lio.gov.on.ca) | Stream segments + connectivity graph, barriers | Ontario |
| [PWQMN](https://data.ontario.ca/dataset/provincial-stream-water-quality-monitoring-network) | Dissolved oxygen, pH, temperature, conductivity | Ontario |
| [CABIN](https://www.canada.ca/en/environment-climate-change/services/river-monitoring.html) | Benthic macroinvertebrate community (EPT) | Ontario |
| Ontario surficial geology (MRD 128) | Substrate class per reach | Ontario |
| MNRF regulations | Fisheries Management Zone rules | Ontario |
| Crown land + provincial parks | Public access boundaries | Ontario |

Water chemistry is used as a **constraint, never a confirmation**: readings can rule a species out of a reach, but passing them only means the water is habitable, not occupied.

### Conservation features

- **Species at Risk flagging** — SAR-listed species surface a conservation note wherever they are mentioned. Targeting them is not suggested.
- **Wild vs. stocked distinction** — stocked fish are planted where trucks can reach, not where habitat is best. Stocking records are kept separate from presence evidence.
- **Obscured observations handled honestly** — iNaturalist geoprivacy fuzzes coordinates to ~22 km. Those records are neither discarded nor treated as precise; they contribute soft evidence across the obscuration radius.
- **Presence vs. pressure** — tracked as separate signals, never collapsed into one score.
- **Indigenous/First Nations waters** — flagged as a separate jurisdiction. The bot does not predict within them.

## Current status

**Phase 1 (data layer) is complete.** All ingestion adapters are built and verified — 19 sub-phases covering observations, hydrology, water chemistry, benthic health, substrate, thermal regime, regulations and access.

**Phase 2 is in progress.** The SDM retirement described above landed here; what replaced it is the central context layer:

- `describe(place)` — everything known about one stretch of water, in slices (records, water, structure, access, conditions, personal history), each field carrying provenance and an empty-reason
- `explore(area)` — ranks water you haven't fished by observation scarcity, structure, access and remoteness, with **no habitat-quality term**. A plausibility gate rules segments out on affirmative evidence — a mapped ditch, a measured hypoxic reading — but never ranks them up
- `user_layer(user)` — patterns, demonstrated expertise and known gaps, derived from logged activity

Everything that reaches the model now goes through it, and a single renderer turns context into text — so a source or an empty-reason cannot be dropped by one call site formatting its own prompt. The records lookup escalates in Python: local corpus, then a live web search tagged unverified, then an honest empty with a specific reason.

The agent's tool surface shrank from 32 tools to 14 as a result. `describe_place` absorbed fourteen per-dataset lookups that each answered a fragment of the same question. Two tools were deleted rather than migrated: one generated gear advice with no source, and one returned corpus trivia.

The model training code survives as a research tool, off the request path.

Also in Phase 2: accessibility scoring, untapped-potential ranking, spot discovery, and a satellite-imagery screening pass.

### Known limitations

Documented honestly rather than papered over — see `CLAUDE.md` for the full list:

- Access scores are only meaningful within ~55 km of home, where OSM access data was ingested. Beyond that, `explore()` rankings collapse into large ties, and the response reports how many candidates tied rather than presenting an arbitrary top ten as "best".
- `stream_order` is unpopulated on all ingested OHN segments — an adapter gap, reported as such rather than as missing data.
- Conservation Authority fish community data (the strongest unbiased ground truth) is not publicly available; MNRF's Broadscale Monitoring records live in an internal database with no public API.
- Non-Ontario jurisdictions (BC, AB, QC, national) are stubbed and frozen.

## Tech stack

- **Python 3.11+** with `uv` for dependency management
- **[Anthropic Claude API](https://www.anthropic.com)** — `claude-sonnet-4-6` default, with a router that sends classification and extraction to cheaper models
- **SQLite** via `sqlite-utils` for local storage
- **Pydantic v2** for all data models
- **FastAPI** + **React/Vite** for the web app
- **Typer** for the CLI
- **pytest** + **ruff** — 1,018 tests

## How to run

```bash
# Install dependencies
uv sync

# Start the chat bot
make run

# Serve the web app + API locally — open http://localhost:8000/app
make serve

# Run all ingestion adapters around home
make ingest

# Refresh every area in data/ingest_areas.json (the weekly ingest)
make weekly-ingest

# Rebuild the explore map's named fishing stretches from data/curation/
make build-stretches

# Run tests
make test

# Lint / format
make lint
make format
```

Requires a `.env` file with:

```
ANTHROPIC_API_KEY=your_key_here
```

Optional keys unlock individual adapters and features:

| Key | Used by |
|---|---|
| `EBIRD_API_KEY` | eBird piscivore observations |
| `MAPBOX_TOKEN` | Satellite imagery links and map rendering |
| `YOUTUBE_API_KEY` | YouTube transcript ingestion |
| `DATASTREAM_API_KEY` | DataStream water quality |
| `FISHBOT_API_KEY` | Web API authentication |

Note that `.env.example` currently documents only `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `LOG_LEVEL` and `DATASTREAM_API_KEY` — the rest are read by code but undocumented there.

### Everything runs locally

There is no hosted deployment. `make serve` runs the FastAPI backend (which also serves the built web app at `/app`) on `127.0.0.1:8000` against the local database at `data/fishing.db`; set `DATA_DIR` to keep the database and catch photos somewhere else. `SERVE_HOST=0.0.0.0 make serve` makes it reachable from a phone on the same network.

The weekly data refresh is `make weekly-ingest`. It reads the list of areas (label, jurisdiction, lat, lng, radius and which source bundles to run) from `data/ingest_areas.json` and runs each one through the same service code as the `/ingest/data*` endpoints, one after another, then prints what each dataset stored and what failed. Edit that file to add or drop an area. To preview without fetching anything:

```bash
uv run python -m src.cli.main weekly-ingest --dry-run
```

To run it every week, add a line to your crontab (`crontab -e`), adjusting the path:

```
0 2 * * 0  cd /path/to/fishbot && PATH=$HOME/.local/bin:$PATH make weekly-ingest >> data/weekly_ingest.log 2>&1
```

The command exits non-zero when any dataset failed, so the log shows the run that needs attention.

## Project scope

All data sources are publicly available under open licenses. No scraping of Instagram, Facebook, TikTok, FishBrain, FishAngler or similar platforms — this is enforced in code, not just documented. Synthesising public information is fine; reconstructing deliberately hidden locations is not. Spot discovery output stays personal — there are no export features that would broadcast location lists.

## License

MIT

---

Personal project. Plans to expand for public use once the data layer is solid. Not currently accepting contributions.
