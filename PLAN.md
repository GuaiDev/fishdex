# FishDex: plan from here to beta testers

Plan version 1, written 2026-11-17 against master `5d5c979` (PR #68). This file is meant to be committed at the FishDex repo root as `PLAN.md` and to outlive this session. Every later piece of FishDex work should start by reading section 0 and the phase it belongs to.

Audit evidence behind this plan: the seven beta audits (`data/fishdex-beta-audit-{pins,frontend,explore2,personal,data2,deploy,health}/report.md`), the system architecture, security, Ontario coverage and pipeline scale scouts, and the design scouts for pins, Explore and coaching. Where this plan cites a measured number it was re-checked in the code at `5d5c979` (section 8 says which).

---

## 0. How to read and update this file

**What beta means here.** A small invite-only group of anglers (the exact group is deliberately not fixed here; see OPEN-1) use FishDex on their phones across real outings for several weeks: they log trips (catch or blank), see their own spots, read what each outing taught them, ask the coach, and send feedback. Non-commercial. Ontario only. No public sign-up.

**What beta is not.** Not a second region, not open sign-up, not a managed database, not a store listing, not the aquarium, not social features. Section 6 lists everything deliberately pushed past beta so it does not drift back.

**IDs are stable.** Phases are `BETA-P0` to `BETA-P7`. Work items are `P<n>.<m>`. Decisions are `DL-<n>`. Deferred items are `DEF-<n>`. Never renumber. Never delete. A superseded decision gets a new `DL` entry that names the old one.

**Each work item is one Claude Code prompt.** It names the area of the code, what to build, and the check that proves it is done. When writing the prompt, paste the item, add "read CLAUDE.md and PLAN.md first", and paste the "done when" line as the acceptance test. Items marked **captain** are not prompts: they need the captain's own hands (a CLI login) or a decision.

**Updating the plan as work lands:**
1. Change only the `Status:` token on the item or phase (`not started`, `in progress`, `done`, `cut`). Do not rewrite the item text. If the text turned out wrong, add a one-line `Note (date):` under it.
2. Append one line to the change log in section 9: date, item id, new status, PR URL.
3. New work discovered inside a phase gets the next free number in that phase (`P3.10`). Work that belongs to no phase goes to section 6 as a `DEF` entry or gets a new phase at the end.
4. A decision that changes direction gets a new `DL` entry. A deferred item that comes back into scope needs a `DL` entry saying why.
5. The phase status becomes `done` only when every item in it is `done` or `cut` and the phase's "how we know it is done" check has been run and recorded.

**Sizes.** S = one short prompt, likely one sitting. M = one prompt but a long one, expect a fix round. L = will need two or three prompts; the item says how to split.

---

## OPEN-1. Tester group (not decided)

The captain chose not to hardcode who the beta testers are or how many. Do not write a tester count, region or invite list into code, config or copy. Anything that depends on the group (map coverage for their waters in P5, the canary wave size in P7.2) is written to work for any group, and the captain names the group when P7 starts.

## 1. Current state at baseline (verified 2026-11-17)

What exists and works, in one paragraph each. The audits are longer; this is the part the plan depends on.

**Server.** A FastAPI API with 65 routes (`src/api/main.py`), SQLite on a Fly volume, 27 numbered migrations run once at startup, WAL mode, throttled auth writes, request-id JSON logs, `/health/live` and `/health/ready`, CI running lint and tests on every PR. One LLM gateway (`src/llm/`) with a role-to-model registry (14 roles), an `llm_usage` ledger, a per-user daily allowance in units (50) reserved before the call, an optional server-wide daily cap (`FISHBOT_DAILY_MODEL_CALLS`), an 8-iteration agent loop cap, and request body limits. 14 agent tools. Ingest is CLI-only. A plugin registry covers 16 jurisdictions; Ontario is the only one with curation, spots and status data.

**Map and spots backend.** Curated stretches → anchors → plain-language spots (river mouths, where creeks join, below dams, public fishing; quiet water is wired but produced zero spots in the current build), water-body-first routes, sections, spot cards that withhold every species when the jurisdiction has no status data, a region-generic `coverage-check` command. Seed database: 61 stretches, 464 spots, 51 water bodies, 33,662 stream segments, 7,138 iNaturalist and 3,546 GBIF records, 6,414 shoreline features, **0 species-status rows**, 0 bathymetry.

**Personal map backend.** Pins, trip-to-pin linking (150 m, confirmed/inferred/recalled), per-trip and per-pin ratings, a 12-row conditions panel, a match indicator, and Today sorts, all owner-scoped and tested (`src/services/pins/`, routes under `/pins`). No pin sharing. No Google Maps import. No species filter on Today.

**Trip logging backend.** `POST /log-trip` (free text parsed by Sonnet, or structured `catches_json`, timer fields) and `/log-trip/photo`; `GET /sessions`; `PATCH /sessions/{id}`. After a log, enrichment stores a conditions snapshot and returns at most one follow-up question and one templated "pattern" (slump, species gap, technique). There is no explanation of why an outing went the way it did.

**Personalization backend.** `GET /profile` with inferred orientation, two behaviour signals (explore dial, water type), concept literacy, and declared overrides (`PUT /profile/declared/{key}`). Coaching renders the angler's style into its prompt. For current chat profile storage and legacy-data handling, see [README usage](README.md#how-to-run). No questionnaire. Per-user home delivery is tracked in P0.2 below.

**Species imagery.** A provenance-checked pipeline with 14 approved illustrations of 69 species; every other species gets one shared silhouette; a credits page is generated. Served by `/fishdex`, which hard-codes `CA-ON`.

**Client.** `app/` is a plain static site (no framework): Map, Coach, Field guide (live strip plus an iframe mockup), and two sample tabs (Anglers, Theme). It calls only `/auth/me`, `/auth/signup`, the map routes and `/chat`. **There is no trip logging, no pins, no collection, no profile, no sign-out and no feedback in the client.** Link-token handling is fixed and tested (7 JS tests pass).

**Hosting.** Fly.io API (1 GB, auto-stop, zero minimum machines) plus a Vercel static site. **The hosted API has been unreachable (TLS handshake fails) since at least 2026-11-14; the Vercel page loads.** Railway was dropped on 2026-10-09; only comments mention it. Data reaches the host by copying the whole database file over SFTP, which would overwrite users. Backups are Fly's default daily volume snapshots with no restore drill.

**Test suite.** 2,835 tests collect. On this NixOS machine they need `LD_LIBRARY_PATH` set to a gcc and zlib lib directory (missing `libstdc++.so.6`); with that and fsync off for test databases the full suite runs. `make lint` passes. See section 8 for the run result.

**Open security findings from the security scout, at baseline:** chat sessions have no owner and session ids are client-chosen (S4), the trip-location resolver counts every user's history (S6), CORS defaults to any origin when unset (S7; `fly.toml` sets it), `/docs` is public (S7), five 500 responses return raw exception text (S7), tokens are plaintext and unrevocable (S8), photos are a public static mount with unguessable names (S9), 43 `user_id: int = 1` defaults in 14 files (T18), and the captain's dated trip logs and home coordinate are in the public repo (S10, captain's call).

---

## 2. What a beta needs, and the gaps

| Need | Have | Gap | Phase |
|---|---|---|---|
| A reachable, always-on server | Fly config, readiness check | API down; zero-minimum machines; 1 GB | P1 |
| Tester data cannot be lost or overwritten | Volume snapshots | Seed path overwrites users; no restore drill | P1 |
| Cost cannot run away | Gateway, ledger, per-user units, optional global cap, loop cap | Global cap not set; no prompt caching; budgets are units not dollars (accepted for beta) | P1 |
| Each tester's data is theirs | Owner-scoped pins/trips/catches with tests | 43 `user_id = 1` defaults; chat sessions unowned; resolver unscoped; prompt reads the captain's profile file | P1, P2 |
| Sign up, sign in, sign out | Invite code signup; token login | No sign-out; no logout/revoke; no per-user home (PR open); no questionnaire | P1, P2, P4 |
| The core loop: go, log, see, learn | Backend for logging, pins, conditions, match, collection | **None of it is in the client**; no "what this outing tells us" | P3 |
| Reward going | Collection API, 14 illustrations, silhouette | Silhouette means "no art", not "not caught yet"; no client screen | P3 |
| Honest map data | Fail-closed species screen; coverage-check; provenance labels | Status table empty on the seed; no attribution page; no coverage notice outside the home area; mouth pins offshore; old records | P1, P5 |
| Tester feedback | Nothing | Route, table, button, dashboard view | P6 |
| Privacy and legal basics | EXIF stripped; `/terms` is a glossary | No privacy note; no delete/export; personal data in repo | P1, P2, P6, captain |
| Licensing and attribution | Licences stored per record and per source | Nothing rendered except OSM tile credit; Reddit scraping adapter present | P0, P5 |
| Stability | Migrate-once, WAL, CI | Suite slow without T9; card latency 2.7 s without T3; `new_to_me` 128 s; parquet re-read per call | P0, P1 |
| An operator who can see what is happening | Admin dashboard, ledgers | No one-screen beta status; no runbook | P6 |
| Written record that matches the code | CLAUDE.md, PRODUCT.md, backlog | All three contradict the code (no client, Railway, FishBot, "not for release") | P0 |

Unnecessary for beta and cut or deferred (section 6 has the full list with reasons): pin sharing, Google Maps import, database split, map artifact publishing, card precompute, hydro store and region expansion, presence benchmark, golden LLM set, Postgres, PMTiles, staging environment, bathymetry, aquarium, social, voice, Reddit/YouTube community search.

---

## 3. Phases

Order: P0 → P1 → P2 → P3 → P6 → P7, with P4 and P5 running alongside P3 once their inputs exist. Section 4 draws the dependencies.

---

### BETA-P0: Land what is in flight and make the written record honest

**Status:** in progress

**Goal.** Master carries the hardening work already built, the suite is fast and green, and the three documents every prompt reads (CLAUDE.md, PRODUCT.md, docs/backlog.md) describe the code as it is.

**Why first.** Every later prompt reads CLAUDE.md. Today it says there is no map UI and the project is not for release; a prompt that believes that will build the wrong thing. The four in-flight branches each remove a measured bottleneck and are already through or inside the review pipeline.

**Depends on.** Nothing.

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P0.1 | in progress | Merge PR #66, the ingest-runs ledger (architecture T7). It is open with checks passing and being rebased. | `src/storage`, `src/services/area_ingest.py`, ingest adapters | `ingest_runs` table exists after migration; `make weekly-ingest` writes one row per source; the admin dashboard shows the last run | captain/firstmate |
| P0.2 | in progress | Merge PR #63, per-user home and jurisdiction in the database (T17). Open, checks passing. | `src/storage/database.py` (users: `home_name`, `home_lat`, `home_lng`, `home_jurisdiction`), CLI consumers | A user with a Guelph home gets Guelph-nearest name resolution in a test; captain's row migrated from `data/user_profile.json` | captain/firstmate |
| P0.3 | in progress | Finish and merge the T9 test-speed branch (`fm/fishdex-arch-t9-test-speed`, one commit, 34 files): session template DB, `synchronous=OFF` and `journal_mode=MEMORY` for test DBs only, the own-schema fixtures replaced. | `tests/conftest.py` and the 34 touched test files | Full suite under 5 minutes locally and in CI; `make test` green | M |
| P0.4 | in progress | Finish and merge the T3 spatial-index branch: bbox and centroid columns plus an R*Tree on `stream_segments`; `segments_near` uses the index for radius queries. Name lookup scans the name index, so its cost scales with the number of names. | `src/storage/database.py`, `src/services/context/place.py::segments_near`, the hydro ingest fill | Same segment ids as the old full scan on the fixture; spot cards under 0.3 s on the scratch DB (was 2.7 s mean) | M |
| P0.5 | not started | Reconcile the written record. CLAUDE.md: replace the status line (the client exists in `app/`, hosting is Fly plus Vercel, beta is the goal, keep "no public release" as "no open sign-up"), keep every architectural and ethical rule, add "read PLAN.md" under "Where things live". PRODUCT.md: the client is in this repo, the product is FishDex, users are the beta group. docs/backlog.md: mark the Railway section and the React/Lovable UI plan as history, remove `pin_shares` from the schema list, record pins phases 1–3 as built and 4–5 as deferred. README: one "for beta testers" paragraph. Add this file as `PLAN.md` at the repo root. | `CLAUDE.md`, `PRODUCT.md`, `docs/backlog.md`, `README.md`, `PLAN.md` | A grep for `Railway`, `React`, `Lovable`, `FishBot`, `NOT for public release` finds only lines marked as history; a reader of CLAUDE.md alone can name the client directory, the host, and the beta goal | S |
| P0.6 | not started | Repository hygiene. Delete the two tracked zero-byte `fishbot.db` files. Move the personal-history scripts (`batch_log_historical_trips*.py`, `relog_skipped_trips.py`, `correct_angler_context.py`, `enrich_historical_sessions.py`, `seed_angler_context.py`, `migrate_parsed_trips.py`) under `scripts/historical/` with a README saying they are not for beta operators, or delete them if P0.7 chooses so. Set the Reddit source to disabled in the Ontario plugin and fix its user agent (it names another GitHub user). | `scripts/`, `src/ingest/jurisdictions/ca_on/plugin.py`, `src/ingest/community/reddit.py` | `scripts/` top level holds only `build_vercel.sh`, `build_species_images.py`, the `export_*_fixture.py` files, `import_map_segments.py`, `prewarm_cache.py`, `debug_fmz_fetch.py`, `find_fmz_layer.py`; `fishbot ingest` skips Reddit | S |
| P0.7 | not started | **Captain call (security S10).** The captain's dated trip logs and the home coordinate `43.4675, -79.6877` are in the public repo and on Vercel. Recommended: move the trip text to a git-ignored local file, replace the coordinate with a town-centre placeholder, leave history alone. | `scripts/`, `src/models/profile.py:32`, `src/cli/export_map.py`, `design/lavish/*.html` | The captain has answered; the chosen option is applied | captain |

**How we know P0 is done.** CI green on master with all four branches merged; `make test` under 5 minutes; a fresh Claude Code session given only CLAUDE.md and PLAN.md describes the current product correctly.

**Cut if short.** P0.6 and P0.7 can slide into P1. P0.3 cannot: a 40-minute suite makes every later item slower to validate.

**Risks.** T3 and T9 were written against an older master and may need a rebase; both branches are single commits, so re-applying is cheap if the rebase is messy.

---

### BETA-P1: A host that exists, keeps data, and cannot run away

**Status:** not started

**Goal.** The API answers from a phone on cellular, tester data cannot be lost by a reseed or a crash, the obvious security holes are closed, and model spend has a ceiling.

**Depends on.** P0.1 (ledger) and P0.4 (spatial index) for latency; otherwise independent of P0.

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P1.1 | not started | **Captain.** Bring the Fly API back (Ontario audit S1): `fly status -a fishdex-api-jason`, `fly ips list`, `fly certs list`; allocate an IP or redeploy per `deploy/README.md`. | Fly CLI | `https://fishdex-api-jason.fly.dev/health/ready` returns 200 from a phone on cellular and from three check-host nodes | captain |
| P1.2 | not started | Always-on machine. `min_machines_running = 1`, `memory = "2gb"` (decision D1; the 1 GB box is tight for pandas and the card overlay). Document the monthly cost in `deploy/README.md`. | `fly.toml` | Readiness stays green for 24 hours with no cold start in the logs | S |
| P1.3 | not started | A seed import that never touches user tables. `fishbot import-shared <source.db>`: a table-ownership list (personal / corpus / map) in one module; copy corpus and map tables in one transaction; refuse to write any personal table; print row counts before and after; replace the SFTP whole-file step in `deploy/README.md` with "copy the file to `/data/seed.db`, run `import-shared`". | `src/storage/ownership.py` (new), `src/cli/main.py`, `deploy/README.md` | Test: a target DB holding 2 users, 3 sessions and 2 pins keeps them byte-for-byte after an import that replaces spots, water bodies and species ranges | M |
| P1.4 | not started | Readiness row floors. `/health/ready` fails when the served jurisdiction has no `spots`, `water_bodies`, `species_ranges` or `stream_segments` rows. | `src/api/main.py::health_ready` | A test DB missing `species_ranges` makes readiness return 503 with the table named | S |
| P1.5 | not started | Backup and a tested restore. `fishbot export-users --out <file>` writes only personal tables; a weekly off-site copy of the volume database (`fly ssh sftp get`) is documented with a cron line for the captain's machine; Fly volume snapshots stay on. Run the restore drill once onto a fresh volume and record the date. | `src/cli/main.py`, `deploy/README.md` | `deploy/README.md` has a dated "restore drill passed" line and the exact commands | M |
| P1.6 | not started | Safe defaults (security S7). CORS defaults to loopback origins when `FISHBOT_CORS_ORIGINS` is unset and logs a WARNING when bound to a non-loopback address; `/docs` and `/openapi.json` are off unless `FISHBOT_ENABLE_DOCS=1`; the five `detail=str(e)` 500 responses (`main.py` lines 540, 831, 899, 979, 1094 at baseline) return a generic message plus the request id and log the exception. | `src/api/main.py` | Tests for each: preflight from another origin is refused by default; `/docs` is 404 by default; a raised exception in `/chat` gives a 500 whose body has no path or SQL | S |
| P1.7 | not started | Token hygiene (S8). Store `sha256(token)`; migrate existing rows in place; `POST /auth/logout`; `fishbot revoke-tokens --user <name>`; refuse a token whose expiry cannot be parsed. | `src/auth/auth.py`, `src/storage/database.py` (migration 28), `src/api/main.py`, `src/cli/main.py` | Existing tokens still work after the migration; logout makes the token 401; a malformed expiry is 401 | M |
| P1.8 | done | Photos behind auth and a pixel cap (S9, F12). Replace the `/photos` static mount with `GET /photos/{name}` that serves a file only to the owner of the catch it belongs to; set `Image.MAX_IMAGE_PIXELS` to 40 million and map decompression-bomb errors to 400. | `src/api/main.py`, `src/services/photo_storage.py` | Another user gets 404 for a photo URL; an anonymous request gets 401; a 100-megapixel PNG upload gets 400 | S |
| P1.9 | not started | Chat ownership and resolver scope (S4, S6). Server-issued session ids (uuid4) returned to the client; `chat_sessions` and `chat_messages` carry `user_id` on write; lookups are `(session_id, user_id)`, so a session belonging to someone else starts a fresh one; `_most_fished_candidate` counts only the caller's own stops. The client keeps the returned session id. | `src/storage/conversations.py`, `src/agent/chat.py`, `src/api/main.py`, `src/services/trip_parser.py`, `app/js/chat.js` | User 2 posting to user 1's session id gets a new session and user 1's transcript is unchanged; user 2's ambiguous place name does not snap to user 1's most-logged segment | M |
| P1.10 | not started | Prompt caching and the global cap (T5, D2). Put `cache_control` on the stable system prompt and tool list in the gateway for the `chat` and `coaching` roles, per-user context after the breakpoint. Set `FISHBOT_DAILY_MODEL_CALLS` on Fly to the captain's number (recommended 300 units a day to start). | `src/llm/gateway.py`, `src/agent/chat.py`, Fly secrets | A recorded test shows `cache_read_input_tokens > 0` on the second turn; the `llm_usage` ledger on the host shows cache reads; the cap is set | S |
| P1.11 | not started | Species status on the seed. Load `species_ranges` from `data/processed/ontario_species_ranges.json` on the seed DB, run `verify-species-status` with `data/registry/cosewic_2024.csv` (12 rows), and add Ontario's SARO list to `data/registry/` if a public export exists so more than 12 of 69 are verified. Unverified rows stay unverified; the screen stays fail-closed. | `fishbot` CLI, `data/registry/`, seed DB | Seed has 69 `species_ranges` rows; `has_status_data("CA-ON")` is true; a card beside a Redside Dace record withholds it and names Creek Chub; the count of verified rows is recorded in the change log | M |
| P1.12 | not started | Keep the Explore parquet in memory. `load_cached_untapped` re-reads an 8 MB parquet on every call; cache it in-process keyed on the file's mtime. | `src/services/untapped_potential.py` | A test shows one read for two calls; `explore()` on the seed DB under 1 s warm | S |

**How we know P1 is done.** From a phone on cellular: readiness 200, sign in, one chat answer. On the host: a seed import leaves users intact (checked by row counts before and after). The restore drill line is dated. The security tests from P1.6–P1.9 are green. The ledger shows cache reads and the cap is set.

**Cut if short.** P1.12 and P1.8's pixel cap. Nothing else: every other item protects tester data, cost, or privacy.

**Risks.** The Fly outage cause is unknown; if the app was destroyed, the volume may be gone too and the seed must be rebuilt from the captain's machine (`make build-stretches`, 3.2 GB peak, must not run on the host). Hashing tokens in place must be done with a migration that handles both forms during the switch.

---

### BETA-P2: Every row has an owner

**Status:** not started

**Goal.** A second user's data can never land in the captain's account by accident, and the chat prompt describes the person asking, not the captain.

**Depends on.** P0.2 (per-user home merged).

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P2.1 | not started | Required `user_id` (T18). Remove the 43 `user_id: int = 1` defaults in 14 files; services take `user_id` as a required keyword; the API builds a `UserContext` once per request; seeded dispersal insights move to a shared table; `trips` and `recommendations` get an owner column or are retired. Split: (a) storage and context layer, (b) services and agent tools, (c) API and CLI. | `src/storage/`, `src/services/`, `src/services/context/`, `src/agent/`, `src/api/main.py`, `src/cli/main.py` | `grep -rn "user_id: int = 1" src/` returns nothing; `make test` green | L |
| P2.2 | not started | Tenancy trace test (T19). During the API route tests, trace every SQL statement and fail on any statement touching a personal table without a `user_id` binding; a self-test proves it catches a deliberately broken query. | `tests/conftest.py`, `tests/test_tenancy_trace.py` (new), `src/storage/ownership.py` from P1.3 | The self-test fails on the broken query and passes on master; runs in CI | M |
| P2.3 | not started | The chat prompt is per user. `system_prompt.assemble` reads home, jurisdiction and the blended angler profile from the database; the API never reads `data/user_profile.json` or the legacy `trips` table; `/fishdex`, `_DEFAULT_REGION` and `DEMO_DEFAULT_CENTER` take their jurisdiction from the user's home (demo from a deploy setting). | `src/agent/system_prompt.py`, `src/agent/chat.py`, `src/api/main.py` | A temp-DB user with a Guelph home sees Guelph in the prompt and a CA-ON species pool; `grep -c '"CA-ON"' src/api/main.py` is 0 | M |
| P2.4 | not started | Angler style in chat (coaching scout slice 2). Render `render_angler_style(angler_profile(...))` into the chat system prompt through the context layer, with the same confidence gate coaching uses. | `src/services/context/render.py`, `src/agent/system_prompt.py` | Prompt tests: explorer, regular, unknown; no jurisdiction strings in the style text; a listed species still refuses | S |
| P2.5 | not started | Operator account tools. `fishbot delete-user <username> [--dry-run]` removes every personal row and the user's photos; `fishbot export-user <username> --out <file>` writes their sessions, stops, catches, pins, chat and profile as JSON. | `src/cli/main.py`, `src/storage/ownership.py` | After delete, the P2.2 trace finds no rows for that id and no photo files; export counts match the DB | S |

**How we know P2 is done.** Two users in one temp DB log trips, pin spots and chat; the trace test passes; each sees only their own rows in every route; the prompts differ by home.

**Cut if short.** P2.4 can move to P3. P2.5 can ship as delete only.

**Risks.** P2.1 touches many files; do it in the three listed slices with the suite green between them, and expect the no-mistakes review to find a missed call site or two.

---

### BETA-P3: The core loop in the client

**Status:** not started

**Goal.** A tester, on a phone, logs an outing (catch or blank), sees it on their own map, sees their collection change, and reads what the outing most likely taught them. This is the thesis made visible; without it there is nothing to test.

**Depends on.** P1 (a live host). Client screens can be built against a local API as soon as P0 is done. P3.7 should land after P2.1 so it takes `user_id` the required way.

Design rules for every client item: plain files under `app/` with the same shape as `app/js/map.js` (no framework, no build step); every API call goes through `app/js/api.js`; text goes through `h()` as text nodes, never HTML; one-handed phone layout with 44 px targets; every list has a loading, empty and error state; the three provenance labels the server emits stay visible.

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P3.1 | not started | Log-a-trip screen, structured first. Where: the selected spot or pin, the phone's location, or a map tap. When: a start/stop timer that fills `started_at` and `ended_at`. Catches: species picker from the `/fishdex` pool (search by common name), count, optional size; "No fish" is a first-class choice, not an empty list. Notes: free text, optional. Photo: optional, through `/log-trip/photo`. Submit sends `catches_json` plus the fields; show the returned `pin_links` and the reflection (P3.7) when present. Split: (a) screen and submit, (b) photo and timer. | `app/js/log.js` (new), `app/js/api.js`, `app/index.html`, `app/app.css` | On a phone against the hosted API: a blank and a two-species catch are logged and both appear in `GET /sessions` with the right stop coordinates | L |
| P3.2 | not started | Trips list and trip detail. List from `GET /sessions` newest first, blanks shown as blanks; detail shows catches, the conditions snapshot, the rating control (`PATCH /sessions/{id}`), the times, and the reflection. | `app/js/trips.js` (new) | Rating a trip persists and shows on reload; a blank reads "no fish" not "0 catches" | M |
| P3.3 | not started | My map. A "Mine" toggle on the Map tab draws the user's pins and unpinned stops as faint dots; long-press to create a pin; "Pin this spot" after a log; a pin sheet with facts, the conditions panel (`/pins/{id}/conditions`, 12 rows with source and age), the match indicator (`/pins/{id}/match`), link undo; a Today list (`/pins/today`) with sort choices. Split: (a) pins layer and sheet, (b) conditions and match, (c) Today. | `app/js/pins.js` (new), `app/js/map.js` | Walkthrough: create a pin, log a trip within 150 m, see the inferred link, undo it, re-link; the conditions sheet shows stale values marked | L |
| P3.4 | not started | The Dex (collection) screen, replacing the Field guide iframe mockup. From `/fishdex`: caught species (the user's photo first, else the illustration), uncaught species as silhouettes grouped by family, personal bests. Keep the live "species on record near here" strip. The silhouette is the reward hook (decision DL-8): it fills in when the species is logged. | `app/js/guide.js` becomes `app/js/dex.js`, `app/js/main.js` | A species logged in P3.1 switches from silhouette to the user's photo after reload | M |
| P3.5 | not started | Account screen. Username, usage today, sign out (`POST /auth/logout` then `forgetToken()`), links to the privacy note (P6.3) and attributions (P5.1), the feedback button (P6.1), and the declared-style controls from `/profile` (explore dial with "set by you" marked). | `app/js/account.js` (new), `app/js/main.js` | Sign-out clears the stored token and lands on the connect screen; a revoked token lands there too | S |
| P3.6 | not started | Beta navigation. Drop the Anglers and Theme sample tabs; the app no longer loads any `design/lavish/*.html` iframe. Tabs: Map, Log, Trips, Dex, Coach; Account in the header. Keep `build_vercel.sh` copying Leaflet and the font from `design/lavish/`. | `app/js/main.js`, `app/index.html`, `scripts/build_vercel.sh` | No `<iframe>` in the app; the Vercel build still serves map tiles and the font | S |
| P3.7 | not started | Trip reflection v1: "what this outing tells us". A new service that, after a log, assembles only context-layer slices (conditions at the time from `session_conditions`, records within the card radius, the user's own history at that water, season, the angler style), renders them through `render.py`, and makes one gateway call (role `coaching`, charged to the log-trip reservation) whose instructions require "likely reasons" wording, source labels, no certainty, no catch promises, blanks as information. Store the text on the session; return it from `/log-trip` and `/sessions/{id}`. Errors log at WARNING and return `None`, never swallow. Retire the catch-rate framing in `detect_proactive_patterns` or move it behind the style gate. | `src/services/trip_reflection.py` (new), `src/services/context/render.py`, `src/services/trip_logger.py`, `src/api/main.py`, `src/models/trip.py` | Prompt tests: a blank at a new water reads as a first look, a blank at a known water compares with earlier visits, no "catch more", every fact line carries its label; a failing model call leaves the log saved and `reflection` null with a WARNING | L |
| P3.8 | not started | Exposure log (T28, light). One table `exposures(user_id, kind, item_ids, ranks, shown_at, request_id)` written by signed-in `/map/spots`, `/pins/today` and the chat tool results that name spots; a helper that joins exposures to later stops within 300 m. No UI. | `src/storage/exposures.py` (new), `src/api/main.py`, `src/agent/chat.py` | Rows are written in route tests; `fishbot exposures --user <name>` prints shown versus visited counts | S |
| P3.9 | not started | Coach answers keep their labels. Check that the chat markdown renderer in `app/js/util.js` preserves the RECORD / WEB / INFERENCE markers and empty-reason sentences the server emits, and style them. | `app/js/util.js`, `app/js/chat.js`, `app/app.css` | A fixture reply with all three labels renders them visibly on a phone | S |

**How we know P3 is done.** The tester walkthrough (P6.5) passes end to end on the hosted app from a phone: sign in, log a blank, log a catch with a photo, pin the spot, read the reflection, see the Dex change, rate the trip, ask the coach about the spot and get an answer with labels.

**Cut if short.** P3.3's Today list and match indicator (keep pins and the conditions sheet). P3.9. P3.1's photo path can ship a sitting later. Do not cut P3.7: it is the thesis.

**Risks.** The client is a hand-written static app; six new screens is the largest single body of work in the plan. Keep each screen to one file and one prompt. The reflection's quality depends on Haiku following a long instruction block; review ten real outputs by hand before the first tester sees one, and keep the role switchable in the registry.

---

### BETA-P4: Onboarding and goals, minimal

**Status:** not started (blocked on the captain's questionnaire design, held as `fishdex-goals-model-scout`)

**Goal.** A new tester tells FishDex where home is and how they like to fish, and the coach and map use it from the first answer.

**Depends on.** P0.2 (home per user), P2.3 (prompt per user), and the captain's design for the questions and the goals model.

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P4.1 | not started | Questionnaire storage and route. `questionnaire_answers(user_id, version, key, value, answered_at)` plus `POST /profile/questionnaire` that validates against a versioned question set in `src/models/questionnaire.py`, stores the answers, and writes the matching declared values (`orientation`, `size_focus`, `micro_focus`, `signal:explore`) as fading starting values, and the home via P0.2. | `src/models/questionnaire.py` (new), `src/storage/questionnaire.py` (new), `src/api/main.py` | Model tests; a second submit stores a new version and the declared values update; unknown keys are 422 | M |
| P4.2 | not started | Onboarding screens after signup: home (map tap or a place search through the cached geocoder), the captain's this-or-that questions, species interest; every step skippable. | `app/js/onboard.js` (new), `app/js/main.js` | A fresh invite code leads through onboarding; the first coach answer names the home area and matches the declared style in a prompt test | M |
| P4.3 | not started | A welcome screen that says what FishDex is and is not, in the captain's words: it does not promise fish; it learns from every outing; your spots are private; explanations are likely reasons. | `app/js/onboard.js` | Text approved by the captain; shown once per account | S |

**How we know P4 is done.** A new tester reaches the map with a home set and at least one declared value, and skipping everything still works.

**Cut if short.** Ship with home only (P4.2 first step) and the welcome screen; the questions follow when the design is ready. Do not block P3 on P4.

**Risks.** The questionnaire semantics are the captain's; if the design arrives late, the fallback above keeps the beta viable.

---

### BETA-P5: Honest data on the map

**Status:** not started

**Goal.** Nothing the map or coach says is unsourced, unattributed or wrong about coverage. The hard gates of the coverage check pass on the seed that ships.

**Depends on.** P1.3 (seed import), P1.11 (status rows), coverage-check (merged as #58).

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P5.1 | not started | Attributions page. `GET /attributions` renders every source: the plugin registry's per-source `licence` fields (Ontario OGL and the others), plus the global sources (iNaturalist, GBIF, OpenStreetMap ODbL for tiles and derived features, Open-Meteo, Water Survey of Canada, Wikimedia Commons for illustrations, eBird if any row is served). Link it from the app footer and the map attribution control. On record lines that come from iNaturalist, show the observer name and a link, as CC-BY and CC-BY-NC require. | `src/services/attributions.py` (new), `src/api/main.py`, `src/services/context/render.py`, `app/js/*.js` | A test asserts every registered source appears with a licence and URL; a record line in a card names its observer | M |
| P5.2 | not started | Coverage notice. Outside the built-area bounding box (derived from the curated stretches and the OSM access footprint of about 55 km around home), the map and the coach say plainly that detailed data covers the Toronto–Hamilton–Guelph area and elsewhere only global records exist. The `access not measured here` wording already exists for Explore; reuse the pattern. | `src/services/coverage/`, `src/api/main.py`, `app/js/map.js` | A point near Ottawa shows the notice; a point in Oakville does not | S |
| P5.3 | not started | Run `coverage-check` on the seed DB and close the hard gates still failing. At the Ontario audit: mouth pins offshore (Sixteen Mile 1.7 km, Bronte 2.3 km) and river lines across the lake (Ontario S3: clip at the lake outline and validate curation points), silent Overpass empties (S4: confirm empties before caching; check whether #53 already covers it), and the listing table (P1.11). Rebuild off-host, reseed with P1.3. | `src/services/stretches.py`, `src/services/stretch_anchors.py`, `src/ingest/global/osm.py`, `data/curation/stretches_ca_on.toml` | `fishbot coverage-check --jurisdiction CA-ON` exits 0 on the hard gates against the seed; the gate table is pasted into the change log | L |
| P5.4 | not started | **Captain call** (Ontario audit call 1, held as `fishdex-home-coverage-scope`). Add the chosen home waters to the curation file, rebuild, reseed. Recommended option (b): the Lake Ontario rivers plus the dammed inland creeks. | `data/curation/stretches_ca_on.toml` | The focus-area gates for the chosen waters pass | M after the call |
| P5.5 | not started | **Captain call** (Ontario audit call 3). Old records: group records older than a cutoff (1980 recommended) under a collapsed "historical" heading on cards. | `src/services/spot_card.py`, `src/services/context/render.py`, `app/js/map.js` | A card with 1910s records shows them only under the heading | S after the call |
| P5.6 | not started | Junction titles (Ontario S9): same name on both sides reads "where two branches of X meet"; direction from upstream length, not stretch role. | `src/services/spots.py` | Fixtures from Duffins, Niagara/One Mile, Welland/Lyons pass | S |
| P5.7 | not started | Unverified status label. Where a species' status row has no `status_source`, the card and coach say "status not yet verified against an official list" instead of silence. | `src/services/context/render.py`, `src/services/species_labels.py` | Render test on a row with and without a source | S |

**How we know P5 is done.** Coverage-check hard gates pass on the shipped seed; the attributions page lists every source; a tester outside the built area sees the notice; no species is named without status data.

**Cut if short.** P5.6 and P5.5. P5.4 depends on the captain and can land during beta as a data refresh through P1.3.

**Risks.** Rebuilding the map runs only on the captain's machine (3.2 GB peak); the pipeline-scale work that would fix that is deferred (DEF-5), so budget time for the build and use the cgroup cap from the pipeline scout if the machine is shared.

---

### BETA-P6: Feedback, visibility and the runbook

**Status:** not started

**Goal.** Testers can tell the captain what happened, the captain can see the state of the beta on one screen, and anyone can operate the host from a written page.

**Depends on.** P3 (client screens exist to hold the feedback button and links).

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P6.1 | not started | Feedback. `POST /feedback` (text, page, optional request id) into a `feedback` table with `user_id`; an admin dashboard section listing unread feedback; a button on every screen's header that opens a short form. | `src/storage/feedback.py` (new), `src/api/main.py`, `src/services/admin_dashboard.py`, `app/js/feedback.js` (new) | A tester sends feedback from the phone and it appears on `/admin/dashboard` with their username and the page | S |
| P6.2 | not started | One-screen beta status. `fishbot beta-status` prints: readiness, users and last-seen, sessions in the last 7 days, model spend in the last 7 days from `llm_usage`, unread feedback, last ingest run, and the count of 5xx lines in the last day of logs. | `src/cli/main.py`, `src/services/admin_dashboard.py` | The command runs against the host over `fly ssh console` and prints all seven numbers | S |
| P6.3 | not started | Privacy note. `GET /privacy` in plain language: what is stored (trip locations, catches, photos without GPS tags, chat text, profile answers), who can see it (only you and the operator), where it lives, how to get a copy or have it deleted (P2.5), what is sent to the model provider. Linked from Account and the connect screen. | `src/api/main.py`, `src/services/terms.py` or a new static page, `app/js/account.js` | The page is served and the captain has read and approved the text | S |
| P6.4 | not started | Beta runbook. `docs/beta-runbook.md`: invite a tester (`fishbot invite`, `POST /admin/invite`), what to tell them, the weekly checks (P6.2), cost watch and what to do at the cap, refresh the map (P1.3), backup and restore (P1.5), what to do when readiness fails, how to revoke a token, how to delete a user. | `docs/beta-runbook.md` (new), `deploy/README.md` | Firstmate follows the runbook end to end once on the live host without asking a question | S |
| P6.5 | not started | Tester walkthrough and go/no-go checklist, kept in this file as section 7. | `PLAN.md` | The walkthrough is run on the hosted app from a phone and the result is recorded in the change log | S |

**How we know P6 is done.** A feedback message round-trips; `beta-status` runs on the host; the runbook has been followed by someone other than its author.

**Cut if short.** P6.2 can be a dashboard section only. P6.4 can be short. P6.1 cannot be cut: without it there is no beta, only usage.

---

### BETA-P7: Gate and launch

**Status:** not started

**Goal.** Decide, on evidence, that testers can be invited, then invite them in two waves.

**Depends on.** P0–P3, P6; P4 at least the home-only fallback; P5 hard gates.

| ID | Status | Work | Where | Done when | Size |
|---|---|---|---|---|---|
| P7.1 | not started | Run the gate: section 7's checklist, every line with a date and a result. | `PLAN.md` section 7 | Every hard line is green or has a captain-signed exception | captain/firstmate |
| P7.2 | not started | Canary wave: two or three testers for one week. Read feedback daily; fix; record in the change log. | runbook | A week with no data loss, no runaway cost, no cross-user report | captain |
| P7.3 | not started | Full wave: the rest of the group. Weekly cadence: feedback, `beta-status`, cost, and a change-log entry. | runbook | Recorded weekly for the beta's length | captain |

---

## 4. Dependency map

```
P0.1 ledger ──────────────────────────────┐
P0.2 user home ──► P2.3 prompt per user ──► P4.1/P4.2 onboarding (needs captain design)
P0.3 test speed ──► everything after (faster validation)
P0.4 spatial index ──► P1 latency check; new_to_me decision DL-18
P0.5 docs ──► every later prompt reads the truth

P1.1 host back ──► P1.2 always-on ──► P3 walkthrough on the host
P1.3 seed import ──► P1.11 status rows ──► P5.3 coverage gates ──► P5.4 home curation
P1.5 backup/restore ──► P7.1 gate
P1.6–P1.9 security ──► P7.1 gate
P1.10 caching + cap ──► P7.1 gate

P2.1 required user_id ──► P2.2 trace test ──► P7.1 gate
P2.1 ──► P3.7 reflection, P3.8 exposure log
P2.5 delete/export ──► P6.3 privacy note

P3.1 log ──► P3.2 trips ──► P3.7 reflection shown
P3.1 ──► P3.3 pins ("Pin this spot")
P3.1 ──► P3.4 dex (silhouette fills in)
P3.5 account ──► P6.1 feedback button, P6.3 privacy link
P3.* ──► P6.5 walkthrough ──► P7.1 gate

P5.1 attributions ──► P7.1 gate (licence condition)
P5.2 coverage notice ──► P7.1 gate
```

Parallel tracks once P0 is done: (1) P1 host and security; (2) P3 client screens against a local API; (3) P5 data work on the captain's machine. P2 sits between P1 and the P3 backend items.

---

## 5. Decision log

| ID | Decision | Why |
|---|---|---|
| DL-1 | Beta is Ontario only, invite-only, non-commercial. Who and how many testers is an open decision (OPEN-1), not fixed by this plan. | The seed holds spots only for the Toronto–Hamilton–Guelph area, so testers outside it see a thin map with a coverage notice (P5.2); iNaturalist records are mostly CC-BY-NC; Open-Meteo and eBird are non-commercial tiers. A wider or commercial beta would need licensing work that is not product work. |
| DL-2 | Stay on SQLite on one Fly machine through beta; no Postgres, no database split, no stateless API. (Architecture D3 option b, assumed; the captain's call stays open but does not change this plan unless he picks "now".) | WAL, migrate-once, the auth throttle and CLI-only ingest remove the measured lock failures. Ten users do not need more. T31/T32 remain the mechanical path later. |
| DL-3 | Replace the database split (T11) and map-artifact publishing (T12) with a table-scoped seed import (P1.3) and readiness row floors (P1.4). | The danger T11/T12 address is "a reseed wipes users". A table-ownership list and an import command remove that danger in one prompt instead of a multi-PR restructuring. The ownership list is also what the tenancy trace test (P2.2) needs. |
| DL-4 | Pin sharing is not in beta, and the recommendation is never. | CLAUDE.md's ethical rule ("spot discovery output stays personal, no export features that broadcast spot lists") and the personal-map design scout's D7 both say never; the pins backlog phase 4 was written before that rule was decided. No schema or code exists for it. |
| DL-5 | Google Maps import is deferred past beta (DEF-1). | Takeout list CSVs carry no coordinates, the parser needs the captain's own export as a fixture first, and a triage screen is a sixth client screen. Testers can pin spots by hand in P3.3. |
| DL-6 | Trip logging in the client is structured first; free text is a notes field, not the primary input. | Structured input costs no model call to parse, cannot mis-place a stop, and is faster one-handed. The text parser stays for the coach's `log_trip` tool. |
| DL-7 | The trip reflection (P3.7) is a context-layer composition plus one model call, worded as likely reasons. | This is the thesis item ("learning made tangible"). Building it from slices through `render.py` keeps it inside the one rule that has prevented prompt drift; a template-only version would read the same for everyone. |
| DL-8 | Silhouettes mean "not caught yet". A caught species shows the user's photo first, then the illustration, then the silhouette. | The captain's vision names silhouettes of uncaught species as the reward hook. Today the silhouette only means "no art"; P3.4 changes the meaning by what the Dex shows, with no change to the image pipeline. |
| DL-9 | Species status: ship the seed with all 69 rows loaded and as many verified as public registries allow; keep the fail-closed screen; label unverified rows. | With zero rows the map names no fish at all, which is safe but empty. With rows and the existing gate, listed species are withheld and the rest are named with an honest label. |
| DL-10 | Drop the Anglers, Theme and Field-guide mockups from the beta client. | They are design samples with sample data; a tester cannot tell them from the product. |
| DL-11 | Spend control stays in units, not dollars, for beta; set the global daily cap; turn on prompt caching. | Units are already enforced atomically; dollar budgets need pricing tables that drift. Caching halves repeated prompt cost with one change. The ledger records estimated dollars for the captain to watch. |
| DL-12 | Backups are Fly snapshots plus a weekly off-site copy plus a tested restore; Litestream is deferred unless the captain chooses architecture D1(a) with object storage. | A tested restore is the thing that matters; continuous replication is better but adds a sidecar for a beginner operator to maintain. |
| DL-13 | Region expansion, the hydro store, the presence benchmark, the golden LLM set and the personalization evaluation are deferred (DEF-4 to DEF-8). | None changes what a tester in Oakville experiences. Each is a measurement or a scale fix that needs beta data or a second region to be worth running. |
| DL-14 | The exposure log (P3.8) ships in beta even though its evaluation does not. | It is one table and three write sites; without it the beta produces no data for the later evaluation. |
| DL-15 | Photos move behind an authenticated route. | Unguessable names are a reasonable defence, but a tester's catch photo should not depend on it, and the change is small. |
| DL-16 | Onboarding ships with home-first and skippable questions; it does not block the core loop. | The questionnaire design is the captain's and is held; the core loop must not wait on it. |
| DL-17 | No staging environment for beta; canary testers instead. | A second Fly app doubles the cost and the operator load; two friendly testers for a week catch the same class of problem. |
| DL-18 | `new_to_me` stays only if, after P0.4, the filter over the home box answers in under 2 s; otherwise hide it for beta and defer card precompute (T13). | Measured 128 s per request at baseline because it runs `describe()` per spot; the spatial index may be enough, and the honest test is to measure. |
| DL-19 | Coverage beyond the home area is a notice, not a filter. | Presence, structure and remoteness are still real outside the OSM footprint (CLAUDE.md Phase 2d); only the access term is unmeasured, and the renderer already says so for Explore. |
| DL-20 | The public read-only demo stays on. | The species leak that made it dangerous (F4) is closed by #59; no demo route reaches a model; it is useful for showing testers what they are joining. |

---

## 6. Deferred past beta

Each entry names why it is out and what would bring it back. Nothing here should appear in a beta prompt.

| ID | Item | Why not now | Revisit when |
|---|---|---|---|
| DEF-1 | Google Maps import (pins step 0, phase 5) | No coordinates in Takeout CSVs; needs the captain's own export as a fixture; a triage screen | A tester asks for it, or after beta when onboarding friction is measured |
| DEF-2 | Pin sharing (pins phase 4) | Conflicts with the ethical rule; recommendation is never | Only by an explicit captain decision that changes the rule |
| DEF-3 | Species filter on Today | Index exists, nothing else; low value with few pins | After beta feedback |
| DEF-4 | Region expansion: namespaced ids (T21), taxonomy and status by authority (T22), no CA-ON literals outside the API (T23), coverage manifest (T24), km distances (T25), second region (BC) | Beta is Ontario only; each is a week of work with no tester-visible effect | When the captain opens a second region (architecture D4) |
| DEF-5 | Pipeline scale stages 1–5: hydro store ingest, numpy accumulation, windowed builds, adapter protocol | The map builds on the captain's machine at 3.2 GB; fine for one region | Before any build larger than the Ontario bbox |
| DEF-6 | Presence benchmark and pressure-as-effort (T26, T27) | A measurement of the corpus, not a product change; quiet-water spots are zero in the current build | After beta, before Explore is promoted on the map |
| DEF-7 | LLM golden set in CI (T30) | Guards model and prompt changes; worth having, but every beta prompt change is reviewed by hand | Before switching the chat model (for example to Sonnet 5.5) or when prompts change weekly |
| DEF-8 | Personalization evaluation (T29) | Needs the exposure log to have beta data | After one month of beta data |
| DEF-9 | Database split by lifecycle (T11), map artifact with manifest (T12), card precompute (T13), Litestream (T14), Postgres with row-level security (T31, T32), stateless multi-instance (T35), PMTiles (T34), photos to object storage (T33) | Scale machinery for users FishDex does not have yet | Architecture D3's trigger: a second API instance, about 50 active users, or open sign-up |
| DEF-10 | Bathymetry and depth-derived spots | Zero contours in the build; code exists | When a lake-focused tester group exists |
| DEF-11 | Quiet-water spots on the map | Zero in the current build; the ranking is honest but says nothing about fish | After DEF-6 |
| DEF-12 | Reddit and YouTube community search | Reddit scraping sits against its terms; zero posts cached; the `search_community` tool is a cost with no corpus | Only with an authorised API and a reason |
| DEF-13 | Aquarium, premium tier, social feed, friends' Dex comparison, voice logging, satellite imagery, SDM re-opening | Product ideas with no beta role; the SDM stays dormant by decision | Not before beta ends |
| DEF-14 | Falls and rapids spot kind, month bars, TRCA surveys as evidence | Data and adapter work; TRCA's portal was unresponsive in May 2026 | When the portal is reliable; after beta |
| DEF-15 | Full field-guide screen (species pages with ranges, look-alikes) | The Dex (P3.4) covers the collection; a guide is a content project | After beta |
| DEF-16 | Staging environment and rolling deploys | DL-17 | With DEF-9 |
| DEF-17 | Server-side chat history and cached web escalation (rest of T6) | History size is capped already; escalation is rare and signed-in only | When the ledger shows escalation or history cost mattering |
| DEF-18 | Fixing the remaining silent-diagnostic sites named in CLAUDE.md (eBird and geology row drops, coverage fraction not stored) | Frozen sources or build-only; none affects a tester | Opportunistically, or with DEF-4 |

---

## 7. Beta gate: tester walkthrough and go/no-go checklist

Run on the hosted app from a phone on cellular, with a fresh invite code. Record the date and result in the change log.

**Walkthrough (every step must work):**
1. Open the Vercel link, see the demo, tap Sign in, enter the invite code and a username.
2. Onboarding: set home (and answer or skip the questions). Land on the map near home.
3. Tap a spot. The card names species only with a source label, says what is special here, and shows "getting there".
4. Log a blank at that spot: timer start, no fish, one note. See it in Trips as a blank with a reflection.
5. Pin the spot from the trip. See it under Mine. Open the pin: facts, conditions with ages, no match yet.
6. Log a catch with a photo and two species. See the Dex change: one silhouette becomes a photo.
7. Rate the trip. Reopen the pin: the trip is linked and the rating shows.
8. Ask the coach about the spot. The answer carries source labels and the angler style is consistent with what was declared.
9. Send feedback from the Account screen. See it on the admin dashboard.
10. Sign out. The token no longer works.
11. From a second account, try the first account's pin id, session id and photo URL: all 404.

**Hard lines (any red stops the launch):**
- Readiness 200 from three outside vantage points for 24 hours.
- Restore drill passed and dated (P1.5).
- Seed import test proves user tables untouched (P1.3).
- `FISHBOT_DAILY_MODEL_CALLS` set; prompt caching reads visible in the ledger (P1.10).
- Security tests P1.6–P1.9 green; tenancy trace test P2.2 green; `grep "user_id: int = 1" src/` empty.
- Species status rows present; a listed species beside a spot is withheld (P1.11).
- Attributions page lists every source (P5.1); coverage notice shows outside the home area (P5.2).
- Privacy note served and approved (P6.3); delete-user tested (P2.5).
- Feedback round-trips (P6.1).
- Walkthrough steps 1–11 pass.

**Soft lines (record, do not block):** spot card under 1 s on the host; `new_to_me` decision recorded (DL-18); coverage focus gates for the chosen home waters; ten reflections reviewed by hand and judged honest.

---

## 8. Verification record (what was checked at `5d5c979`, and what was not)

**Checked in the code or by running it:**
- Route count (65), tool count (14), migration count (27), LLM roles (14), spend guard reservation (`src/auth/auth.py:187-226`), loop cap (8), body limits, migrate-once, WAL, request logs, readiness body.
- `user_id: int = 1` defaults: 43 in 14 files. `"CA-ON"` literals outside the Ontario adapter: 36 in 28 files. `cache_control`: 0. `messages.create` outside `src/llm`: 0. `except Exception` followed by `pass`: 32.
- Seed DB row counts (read-only): species_ranges 0, spots 464, water_bodies 51, stream_segments 33,662, hydro_segments 0, observations 7,138 (5,202 CC-BY-NC, 537 CC-BY, 146 CC0, 1,199 null), gbif 3,546, shoreline 6,414, bathymetry 0, stocking 0, water quality 0, regulation chunks 0, users 1, sessions 0; no `llm_usage` or `ingest_runs` table yet.
- Client: five tabs, seven API routes called, no logging/pins/collection/profile/sign-out/feedback code; `forgetToken` exists but is unreachable. JS link tests 7/7 pass under Node 24.
- Pins: service files and 19 `/pins` and session routes present; no `pin_shares`; no Google import code.
- Profile routes present; questionnaire mentioned only in docstrings; coaching renders angler style; chat prompt reads `UserProfile` and the legacy trips table.
- Species images: 14 approved rows in `selection.csv`, 57 files under `src/api/static/species`, credits page present; `/fishdex` hard-codes CA-ON.
- Security residues: CORS default `*`, no `docs_url` setting, five raw-detail 500s, `/photos` static mount, no sha256 in auth, no logout route, malformed expiry swallowed, resolver unscoped, `start_session` takes no user id.
- Hosting: `fly.toml` 1 GB / min 0 / readiness check; Vercel build script; Railway mentioned only in comments and the backlog; hosted API TLS EOF on `/health/live` and `/health/ready` on 2026-11-17; Vercel `/app/` 200 with `config.js` pointing at Fly.
- Docs: PRODUCT.md says API-only and FishBot; CLAUDE.md line 3 says map UI deferred and not for release; README describes the public demo and Fly/Vercel; CI runs lint and test; zero tracked `__pycache__` files (the health audit's claim is stale); two tracked zero-byte `fishbot.db` files; the personal trip scripts are present.
- PRs: #66 and #63 open with checks passing; T3 and T9 exist as single-commit local branches; no T18 branch.
- Ontario audit ships: S2 coverage-check merged (#58), S5 fail-closed merged (#59), #53 found missed dams; S3, S6, S7, S9 not found in history (not re-measured).
- `make lint` passes. 2,835 tests collect with the `LD_LIBRARY_PATH` workaround. Full suite: 2,831 passed, 4 skipped, 0 failed in 4 min 19 s with fsync off for test databases (the default run is fsync-bound and takes far longer, which is what P0.3 fixes).

**Not verified (treat as open):** the host's actual volume contents and whether the Fly app or volume still exist; card latency on the Fly CPU; the T3 and T9 branches' behaviour after rebase; whether #53 closed the Overpass silent-empty gate; the Ontario audit's placement and recall numbers against the current seed (the coverage-check command exists and P5.3 runs it); the Google Takeout format; Fly and object-storage prices; what Haiku produces for the reflection prompt.

---

## 9. Change log

| Date | Item | Status | Evidence |
|---|---|---|---|
| 2026-11-17 | plan | version 1 written | this file; audits and scouts under `data/` |
| 2026-11-17 | P0.1, P0.2 | in progress | PR #66 and PR #63 open, checks passing |
| 2026-11-17 | P0.3, P0.4 | in progress | branches `fm/fishdex-arch-t9-test-speed`, `fm/fishdex-arch-t3-spatial-index` |
| 2026-11-17 | suite | measured | full suite at `5d5c979`: 2,831 passed, 4 skipped, 0 failed in 4 min 19 s (NixOS `LD_LIBRARY_PATH` workaround plus a scratch plugin setting `synchronous=OFF` on test DBs; the T9 branch makes that plugin unnecessary); `make lint` clean; JS link tests 7/7 |
| 2026-11-19 | P1.8 | done | Owner-only photo route and 40-million-pixel upload cap; acceptance cases covered in `tests/test_api.py` and `tests/test_photo_storage.py`; branch `fm/fishdex-beta-p1-photos-auth` (PR pending validation handoff) |
