# Personalized Fishing AI Bot — Build Roadmap

Historical roadmap from the CLI prototype stage. The current beta work is tracked in [PLAN.md](../../PLAN.md); the current client is described in [PRODUCT.md](../../PRODUCT.md#platform).

---

## Guiding Principles

1. [history] **Ship the CLI bot first, UI last.** Get the brain working before you build the body. A working terminal chatbot that knows about your local fishing scene is more valuable than a half-finished React app.
2. **One data source at a time.** Don't try to ingest Reddit, YouTube, iNaturalist, and forums in week one. Pick one, get the loop working end-to-end, then add the next.
3. **Persist everything.** Cached API responses, your trip logs, scraped pages — store them locally as JSON/SQLite from day one. You'll thank yourself.
4. **Let Claude Code do the boring parts.** Schema design, API wrappers, scrapers, map integration. Save your energy for product decisions.

---

## Recommended Tech Stack

| Layer | Pick | Why |
|---|---|---|
| Language | **Python 3.11+** | Best ecosystem for data wrangling, scraping, and AI |
| AI | **Anthropic Claude API** (claude-sonnet-4-6 to start) | You're already in the ecosystem; tool use + long context |
| Storage (local) | **SQLite + PostGIS-style spatial via SpatiaLite** OR **DuckDB** | Zero-setup, works in Claude Code's sandbox |
| Storage (cloud later) | **PostgreSQL + PostGIS** on Supabase or Neon | Free tier, real spatial queries |
| Vector search | **sqlite-vec** locally, **pgvector** in prod | For semantic search over scraped content |
| Backend (later) | **FastAPI** | Fast to write, plays well with async scrapers |
| Frontend (later) | **Next.js + Leaflet** (free) or **Mapbox GL** (better UX, paid above free tier) | Leaflet for MVP, Mapbox if you outgrow it |
| Mobile (history) | **React Native (Expo)** or **PWA** | Expo if you want app stores; PWA if you don't |

---

## Phase 0 — Project Setup (½ day)

Get the skeleton right so you're not fighting it later.

```
fishing-bot/
├── data/
│   ├── raw/             # raw scraped pages, API dumps
│   ├── processed/       # cleaned, structured
│   └── fishing.db       # SQLite
├── src/
│   ├── ingest/          # one file per data source
│   ├── models/          # Pydantic schemas
│   ├── agent/           # Claude API wrapper + tools
│   ├── memory/          # user profile, trip log
│   └── cli.py
├── prompts/             # system prompts as .md files
├── tests/
├── .env                 # API keys
└── pyproject.toml
```

In Claude Code, start with: *"Set up this project structure with uv, add anthropic, httpx, sqlite-utils, pydantic, and beautifulsoup4. Create a Makefile with `make run`, `make ingest`, `make test`."*

Add a `CLAUDE.md` at the root describing the project, your fishing context (species, location, gear), and conventions. Claude Code reads it automatically.

---

## Phase 1 — Minimum Viable Bot (2–3 days)

**Goal:** A terminal chatbot you can talk to about fishing, that remembers your preferences.

1. Write a basic CLI loop that calls Claude API with a system prompt like *"You are a fishing assistant for an Ontario angler who fishes for [species] within [range] of Toronto."*
2. Store user profile (location, target species, gear, budget, skill level) as JSON in `data/user_profile.json`. Load it into the system prompt every turn.
3. Add a `trips` table: date, location (lat/lng), species, conditions, what worked, what didn't. Build commands like `/log` and `/recent`.
4. **No data sources yet.** This phase is purely about the conversation feel and the personalization plumbing.

You'll know it's done when you can say "what should I try this weekend?" and get an answer that references your last trip and gear.

---

## Phase 2 — First Real Data Source: iNaturalist (2–3 days)

iNaturalist is the easiest win — clean API, no auth needed for reads, real geo-tagged species data.

1. Build `src/ingest/inaturalist.py` that queries observations within a bounding box around your fishing spots, filtered to fish taxa (taxon_id 47178 covers Actinopterygii).
2. Cache results in SQLite with columns: `observation_id, species, lat, lng, observed_on, user, photo_url, quality_grade`.
3. Expose this to the bot as a **tool** (function calling). Claude can call `get_recent_observations(lat, lng, radius_km, days_back)` mid-conversation.
4. Now ask the bot: *"What's been caught near Lake Simcoe in the last 30 days?"* and watch it actually answer.

This is the moment the bot stops being a chatbot and starts being useful.

---

## Phase 3 — Government & Regulatory Data (2 days)

For Ontario specifically:

- **MNRF Fish Stocking data** — published as downloadable CSVs on the Ontario open data portal. Pull, normalize, store.
- **Fishing regulations** — the regulations summary is published as PDFs by zone. Parse with `pypdf` or `pdfplumber`, chunk by zone/species, embed for RAG.
- **Fish ON-Line** — Ontario's interactive map data. Check if there's a public endpoint; otherwise their data layers are often downloadable.

Add tools: `get_stocking_history(waterbody)`, `get_regulations(zone, species)`.

This is the most legally bulletproof data — it's public and meant to be used.

---

## Phase 4 — Community Sources via RAG (3–5 days)

Now the harder, fuzzier stuff. The pattern is the same for all of these: **scrape/fetch → chunk → embed → store in vector DB → retrieve at query time.**

### Reddit
- Use the official Reddit API (free tier exists, you'll need an app registration). Target subs: r/FishingOntario, r/Fishing, r/bassfishing, etc.
- Pull posts + top comments, filter by location keywords.
- Store with metadata: subreddit, score, date, extracted locations.

### YouTube
- YouTube Data API v3, free quota of 10k units/day.
- Search for "[lake name] fishing", grab video metadata + transcripts (via `youtube-transcript-api`).
- Transcripts are gold — they contain detailed how-to and where-to info.

### Forums (Ontario Fishing Community, etc.)
- Check robots.txt first. Most allow respectful crawling.
- Use `httpx` + `beautifulsoup4` with a 1–2 second delay between requests.
- Identify yourself in the User-Agent.

### What to skip
- **Facebook groups** — scraping violates ToS, content is gated, and even if you got it the quality-to-effort ratio is poor. Skip.

### The RAG pipeline
1. Chunk content (~500 tokens, with overlap).
2. Embed with `voyage-3` (best for retrieval right now) or OpenAI's `text-embedding-3-small` if you want cheap.
3. Store vectors in `sqlite-vec` locally.
4. Add a `search_community_knowledge(query, location?)` tool the bot calls when relevant.

---

## Phase 5 — Interactive Map (3–4 days)

This is where it stops feeling like a chatbot and starts feeling like an app.

1. Spin up a minimal **FastAPI** backend that exposes your SQLite data as JSON endpoints.
2. Build a **Next.js** page with **Leaflet**:
   - Base layer: OpenStreetMap
   - Overlay: stocking points (clustered), iNaturalist observations (colored by species), your trip log
   - Click a waterbody → side panel with regs, recent reports, stocking history, and a "Ask the bot about this spot" button that pre-fills the chat
3. Add filters: species, date range, source.
4. The chatbot lives in a side panel and can read the map state (selected waterbody) as context.

For "vibe coding" this part: tell Claude Code *"Build a Next.js app with Leaflet showing markers from `/api/observations`. Side panel with chat. Selecting a marker updates a `selectedWaterbody` context that's passed to the chat."* Iterate from there.

---

## Phase 6 — Personalization & Trip Planning (ongoing)

Once the foundation is solid, the AI features get fun:

- **Trip planner tool:** given a weekend and budget, the bot generates 2–3 options with drive time, expected species, gear suggestions, license requirements, and recent reports.
- **Gear budget tracker:** log purchases, the bot warns when you're over budget or suggests cheaper alternatives based on community posts.
- **Conditions awareness:** add weather (Open-Meteo, free), moon phase, water temperature where available. Tool: `get_conditions(lat, lng, date)`.
- **Learning loop:** after each trip, the `/log` command asks structured follow-ups ("was the bot's suggestion useful?") and that feedback feeds future recommendations.

---

## Phase 7 — Web App → Mobile (when you're ready)

For current hosting and deployment instructions, see [deploy/README.md](../../deploy/README.md).
- Migrate SQLite → Postgres on **Supabase** (free tier, includes auth and PostGIS).
- [history] For mobile: easiest path is making the web app a **PWA** first (installable, offline-capable). Only go React Native / Expo if you need camera/GPS integration that PWAs handle poorly.

---

## Working Effectively in Claude Code

A few habits that pay off:

1. **Write a tight `CLAUDE.md`** with your project conventions, the data schema, and your fishing context. Update it as the project evolves.
2. **Use plan mode** (Shift+Tab) before big changes. Have Claude write the plan, you approve it, then execute.
3. **Commit constantly.** After every working feature. Claude Code can mess things up; git is your undo button.
4. **One scraper at a time, in its own PR-sized chunk.** Test it standalone before wiring into the agent.
5. **Keep prompts in files, not strings.** `prompts/system.md`, `prompts/trip_planner.md`. Easier to iterate, easier to diff.
6. **Write tool descriptions like docs.** Claude's tool use is only as good as the descriptions. Spend time here.
7. **Cache aggressively.** Every external API call should hit cache first. Saves money and makes dev fast.

---

## Realistic Timeline (Solo, Evenings/Weekends)

| Phase | Weeks |
|---|---|
| Phase 0–1 (setup + MVP bot) | 1 |
| Phase 2 (iNaturalist) | 1 |
| Phase 3 (gov data) | 1 |
| Phase 4 (RAG over community) | 2–3 |
| Phase 5 (map UI) | 2 |
| Phase 6 (personalization) | ongoing |
| Phase 7 (deploy + mobile) | 2 |

**~10–12 weeks** to something you'd actually use. **~4 weeks** to a CLI bot that's genuinely useful for your own fishing.

---

## Cost Estimate (Per Month, Personal Use)

- Anthropic API: $10–30 depending on usage
- Embedding API: $1–5
- Reddit API: free tier likely sufficient
- YouTube API: free quota sufficient
- Mapbox: free tier (50k loads/month) — Leaflet is $0
- Supabase/Vercel: free tiers sufficient

**Total for personal use: ~$15–40/month.** Scales if you ever release it publicly.

---

## First Concrete Steps

1. `mkdir fishing-bot && cd fishing-bot && claude` (start Claude Code)
2. Ask it: *"Read the roadmap in `fishing_ai_roadmap.md`. Set up Phase 0 — project structure, dependencies, Makefile, CLAUDE.md scaffold. Use uv for package management."*
3. Once that's clean, move to Phase 1.
