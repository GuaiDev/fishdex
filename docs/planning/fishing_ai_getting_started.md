# Fishing AI Bot — Getting Started in Claude Code

A hands-on walkthrough to go from zero to a working Phase 1 fishing bot. By the end of Day 1 you'll have a CLI bot that knows about your fishing context. By the end of Week 1 you'll have it pulling real iNaturalist data.

This guide assumes you've never used Claude Code before. If you have, skim Sections 1–3 and jump to Section 4.

---

## Section 1 — Prerequisites

You need three things before you start:

### 1.1 A paid Claude account
Claude Code requires a paid plan — Claude Pro ($20/month) is plenty for personal use. Free Claude.ai accounts don't include Claude Code access. Sign up at claude.com if you don't have one.

### 1.2 A terminal and Git
- **macOS:** Terminal app is built in. Install Git via `xcode-select --install`.
- **Windows:** Install WSL2 (Windows Subsystem for Linux). Native Windows works but has friction — WSL2 is worth the 10 minutes. Microsoft has a one-line installer: `wsl --install` in PowerShell.
- **Linux:** You already know.

### 1.3 Python 3.11+ and `uv`
We're using `uv` because it's faster and simpler than pip/venv/poetry. Install:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify: `uv --version`. Should print something like `uv 0.5.x`.

---

## Section 2 — Install Claude Code

The native installer is the current recommended method (zero dependencies, auto-updates):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Or via npm if you prefer (requires Node 18+):
```bash
npm install -g @anthropic-ai/claude-code
```

Verify install:
```bash
claude --version
```

First time you run `claude` in a directory, it opens a browser window for OAuth login. Authenticate with your Claude account. Done — you won't need to re-auth on this machine.

---

## Section 3 — Mental Model: How Vibe Coding in Claude Code Actually Works

Before you type a single command, understand the loop. This saves hours of fighting the tool.

### The four modes you'll use

| Mode | How to enter | When to use |
|---|---|---|
| **Normal mode** | Default | Quick edits, single-file changes, questions |
| **Plan mode** | `Shift+Tab` twice | Before any feature that touches >1 file or needs reasoning |
| **Auto-accept mode** | `Shift+Tab` once | Trusted loops (e.g., writing tests, generating data) where you want it to just go |
| **Focus mode** | `/focus` | Hide intermediate work and only show the final result |

**The single most important habit:** use plan mode before non-trivial features. Tell Claude what you want, let it write a plan, *review the plan*, then say go. This catches misunderstandings before they become wasted code. The cost asymmetry is huge — planning costs near zero, wrong code costs hours.

### CLAUDE.md is your project's brain

When you run `claude` in a directory, it automatically reads any file called `CLAUDE.md` in the project root. This is persistent context — your tech stack, your conventions, your project-specific knowledge. The 2026 consensus is to keep it tight: **50–100 lines, max 200**. Every line should pay rent.

### Model selection matters

- **Opus 4.7** — for planning, architecture decisions, hard problems. Slow, expensive, smart.
- **Sonnet 4.6** — default workhorse. Fast, capable, does ~80% of your tasks well.
- **Haiku 4.5** — for cheap parallel work (subagents, bulk processing). Don't use it for thinking.

Switch with `/model opus` / `/model sonnet` / `/model haiku` inside a session.

### Key slash commands you'll use daily

- `/init` — generate a starter CLAUDE.md from your codebase
- `/clear` — wipe context, start fresh (do this between unrelated tasks)
- `/compact` — compress conversation history to free up context window
- `/model <name>` — switch models
- `/permissions` — manage what Claude can do without asking
- `/agents` — manage subagents
- `/cost` — see what you've spent in this session

### The "evidence before claims" rule

Claude is confident. It will say "tests pass" or "the bug is fixed" without verifying. Don't let it. **Always give it a verification command** — `pytest`, `ruff check`, a curl command, expected output. Make verification part of every task. This single discipline gives a 2–3x quality improvement.

---

## Section 4 — Create the Project

Open your terminal and pick a directory where your code lives. Then:

```bash
mkdir fishing-bot
cd fishing-bot
git init
```

Now copy your four planning documents (the roadmap, advanced capabilities, spot discovery, and feature catalog) into a `docs/planning/` folder inside this project:

```bash
mkdir -p docs/planning
# Copy your four .md files into docs/planning/
```

This matters — Claude Code will be able to read them, reference them, and you can point it at specific sections by filename.

---

## Section 5 — Day 1: First Session

```bash
claude
```

You're now in Claude Code. First thing to do — orient it.

### Prompt 1: Onboard Claude to the project

Paste this exact prompt:

> Read every file in `docs/planning/`. These are my planning documents for a personalized fishing AI bot I'm building for myself in Ontario, Canada. After reading, summarize back to me in 5 bullets: (1) what we're building, (2) the phased approach, (3) the unique angles vs existing fishing apps, (4) the tech stack we agreed on, (5) what Phase 0 and Phase 1 specifically require. Don't write any code yet.

Wait for the summary. Read it carefully. If anything is wrong or missing, correct it now — it becomes the shared understanding for everything that follows.

### Prompt 2: Generate the project scaffold

Now enter plan mode (`Shift+Tab` twice). Paste:

> Based on the planning docs, set up Phase 0 from the base roadmap. Use uv for package management, Python 3.11. Create the folder structure described in the roadmap (src/, data/, prompts/, tests/, etc.). Add a Makefile with `make run`, `make ingest`, `make test`, `make lint`. Add a pyproject.toml with these initial dependencies: anthropic, httpx, sqlite-utils, pydantic, python-dotenv, rich (for nice CLI output), typer (for the CLI), and pytest for testing. Create a .env.example file. Create .gitignore for Python projects. Don't implement any logic yet — just the skeleton. Write me a plan first.

Review the plan. It should match the structure in your roadmap doc. If it's adding stuff that wasn't asked for, push back: "skip X, that's a Phase 2 concern." If it's missing something, add it.

When the plan looks right, exit plan mode (`Shift+Tab` once to auto-accept, or just say "looks good, proceed").

### Prompt 3: Generate a tight CLAUDE.md

> Now create a CLAUDE.md for this project at the repo root. Keep it under 100 lines. It should include: (1) what the project is in 2 sentences, (2) the tech stack and key dependencies, (3) my fishing context (Toronto, Ontario; species: smallmouth bass, brook trout, pike, walleye; primary fishing style: stream + small lakes; skill level: intermediate; primary use case: exploration), (4) the project conventions (uv for deps, ruff for linting, pytest for tests, conventional commits), (5) how to run things (make run, make test), (6) pointers to docs/planning/ as the canonical product spec. Do NOT duplicate planning doc content — reference it by filename. Every line should be something you'd consult while coding.

Review what it produces. Edit by hand if needed — this file matters more than any other.

### Prompt 4: First commit

> Run git add and create an initial commit with a conventional commit message. Verify nothing sensitive is being committed (no .env, no API keys).

You now have a real project. Time to wire up the bot.

---

## Section 6 — Day 1 Continued: The MVP Bot

Stay in the same Claude Code session if context still feels clean; otherwise `/clear` and re-orient with a brief: "Continue working on the fishing bot project. Read CLAUDE.md and the base roadmap. We just finished Phase 0 setup. Now starting Phase 1."

### Prompt 5: Build the CLI loop

Enter plan mode. Paste:

> Implement Phase 1 from the base roadmap: a minimum viable CLI fishing bot.
> 
> Requirements:
> - Use typer for the CLI with commands: `chat`, `log` (log a trip), `recent` (show recent trips), `profile` (view/edit user profile)
> - Use the Anthropic SDK to talk to Claude. Default to claude-sonnet-4-6, allow override via env var.
> - For current profile storage and home ownership, see [README usage](../../README.md#how-to-run).
> - Other profile fields: target_species (list), gear (list of dicts), budget (annual), skill_level, fishing_style, preferences (free text).
> - Store trips in SQLite at data/fishing.db. Schema: id, date, location_name, lat, lng, species_caught (json list), conditions (json), gear_used (json list), notes, what_worked, what_didnt.
> - The chat loop: load user profile into the system prompt every turn. Include the 5 most recent trips as context. Use a system prompt stored at prompts/system.md so I can edit it without changing code.
> - Use rich for nice terminal output. Show typing indicators while Claude responds.
> - Add tests for the storage layer (profile load/save, trip create/list).
> 
> Verification: after implementation, run `make test` to confirm all tests pass, then start `make run` and demonstrate a working chat turn.
> 
> Write me the plan first.

The plan should propose: a few files (cli.py, storage/profile.py, storage/trips.py, agent/client.py, prompts/system.md), pydantic models, tests, and an entry point.

**Push back where needed.** Common things to correct:
- "Make sure the system prompt explicitly mentions I'm fishing in Ontario, Canada — don't let the LLM assume US regulations."
- "Trip logging should be interactive — ask me questions one at a time rather than expecting a flat command."
- "Recent trips in the system prompt should be summarized, not raw JSON dumps — Claude doesn't need the IDs."

When the plan is right, let it execute. Stay engaged — review the diffs as they come.

### Prompt 6: First real conversation

```bash
make run
```

Or `claude` may have already started it. Try:
- "Hi, what do you know about me?"
- "Recommend a good lure for smallmouth in stained water around 65°F."
- `/log` (or whatever the log command is) and log a fake trip to test it.
- "What did I catch on my last trip?"

If responses feel generic or wrong, iterate the system prompt at `prompts/system.md`. You can do this in Claude Code:

> The system prompt isn't making Claude personalize enough. Rewrite prompts/system.md to: (1) explicitly assume Ontario context and regulations, (2) ground recommendations in my logged trip history when available, (3) ask clarifying questions before recommending if it doesn't have enough info, (4) be more like an experienced fishing buddy than a Wikipedia article — short, opinionated, specific.

That's Phase 1 done. Commit. Take a break.

---

## Section 7 — Week 1: First Real Data Source (iNaturalist)

New session is fine here. Open Claude Code in the project, `/clear` if you continued.

### Prompt 7: Reorient and plan iNaturalist ingestion

> Read CLAUDE.md and `docs/planning/fishing_ai_roadmap.md` Phase 2 (iNaturalist).
> 
> Plan an iNaturalist ingestion module:
> - File at src/ingest/inaturalist.py
> - Function: `fetch_observations(lat, lng, radius_km, days_back=30, taxa=['Actinopterygii'])` — returns list of pydantic Observation models
> - Cache responses in data/cache/inaturalist/ as JSON, keyed by query hash, with 24hr TTL
> - Persist deduplicated observations to a new `observations` table in fishing.db
> - CLI command `make ingest` that pulls observations for a configurable area; see [README usage](../../README.md#how-to-run) for home selection.
> - Use httpx async for the API calls, respect their rate limits (1 req/sec is polite)
> - No API key required for iNaturalist reads
> - Tests with a recorded fixture (use vcrpy or just mock httpx) — don't hit the real API in tests
> 
> Write the plan first.

Review. Execute. Then run:

```bash
make ingest
```

You should see real observations land in your database. Verify with:

> Query the observations table and show me the top 10 species observed within 25km of my home location in the last 6 months, with counts.

### Prompt 8: Wire iNaturalist into the chat agent

> Now wire iNaturalist into the chat agent as a tool. Use Claude's tool use feature. Define a tool `get_recent_observations(lat, lng, radius_km, days_back, species_filter?)` that queries the local observations DB. Update the agent client to declare this tool and handle the tool_use response loop. Update the system prompt to mention this capability so Claude knows to use it when relevant.
> 
> Verification: ask the bot "what species have been observed near Lake Simcoe in the last 30 days?" and confirm it calls the tool and produces a real answer.

This is the moment your bot stops being a chatbot and starts being useful. You'll know it works when the bot stops making up species lists and starts citing real, recent observations.

---

## Section 8 — Settling Into a Vibe Coding Rhythm

Now that you've got the loop working, here's how to keep it productive across weeks of iteration.

### Session hygiene

- **New task = new session.** Don't try to do "add weather integration" and "fix the chat bug" in the same Claude Code conversation. Context bleeds across tasks. Use `/clear`.
- **`/compact` mid-task** when the context window starts feeling crowded — Claude tells you when it's getting full.
- **Commit constantly.** After every working unit. Conventional commits help: `feat: add iNaturalist ingestion`, `fix: handle empty observation set`, `docs: update CLAUDE.md`.

### The right loop for each feature

For every new capability, repeat this pattern:

1. **Read the relevant planning doc section out loud to Claude.** ("Read `docs/planning/fishing_ai_feature_catalog.md` Section 5 on tactical recommendations.")
2. **Plan mode.** Have it propose a design. Review and push back.
3. **Implement.** Auto-accept once you trust the plan.
4. **Verify with evidence.** Give it a test command, expected output, or a sample query.
5. **Update CLAUDE.md if the project's shape changed.**
6. **Commit.**

### When Claude goes sideways

It will. Some recovery moves:

- **`/rewind`** if it just made a mess — undoes recent tool calls.
- **Stop it mid-stream** if you see it heading the wrong way. Don't let bad code accumulate.
- **`/clear` and re-orient** if you've corrected the same misunderstanding twice. Sometimes a fresh context is faster than fighting an entrenched assumption.
- **Make verification stricter.** If it claims things work that don't, add a test command to the prompt and demand the output before it declares done.

### The CLAUDE.md update habit

When Claude does something wrong, ask: would a line in CLAUDE.md have prevented this? If yes, add it. Boris Cherny calls this *compound engineering* — every correction becomes a bug that never happens again. Examples of lines worth adding:

- "Never use the global pip — always `uv add` for dependencies."
- "Pydantic models live in `src/models/`, not next to where they're used."
- "When unsure about Ontario regulations, check `data/regulations/` first before guessing."
- "All API responses must be cached to `data/cache/` — never hit external APIs in tests."

Keep it under 200 lines total. When it gets too long, split into `@imports` (e.g., separate files for `docs/conventions.md`, `docs/architecture.md`).

### Cost awareness

Run `/cost` periodically. For Phase 1–2 work, expect $1–3 per evening session. If you see costs spiking, you're probably:
- Using Opus when Sonnet would do
- Forgetting to `/clear` between tasks (huge contexts = expensive every turn)
- Having Claude re-read large files repeatedly instead of caching context

---

## Section 9 — The Phase-by-Phase Build Path

Here's your realistic build path from where you are now, mapping back to the planning docs. Each phase is ~1 Claude Code session for planning + however long the implementation takes.

### Week 1 — Foundation ✓ (covered above)
- Phase 0 setup
- Phase 1 MVP bot
- Phase 2 iNaturalist ingestion + tool wiring

### Week 2 — More data, more tools
- Phase 3 from base roadmap: MNRF stocking data, Ontario regulations PDFs
- Add weather integration (`feature_catalog.md` Section 2)
- New tool: `get_conditions(lat, lng, date)`

### Week 3 — The tactical help-buddy layer
- Lure/bait recommendation engine (`feature_catalog.md` Section 5)
- Color-by-water-clarity rules
- Depth/speed by temperature
- Update system prompt to lean on these recommendations

### Weeks 4–6 — Hydrological network analysis
- This is the big one. From Addendum 2.
- Pull Ontario Hydro Network data
- Build stream graph in networkx
- Add barrier data
- Compute connectivity
- This is where the bot stops being a chatbot and becomes a research tool.

### Weeks 7–8 — Spot discovery
- From Addendum 3.
- OSM + JRC water union for your home region
- Microsoft Building Footprints
- Public land overlays
- Accessibility scoring

### Weeks 9–10 — Habitat suitability model
- The ML layer that ties it together
- Train a random forest on the data you've accumulated
- Output predicted species probability per stream segment

### Weeks 11–12 — Map UI
- Phase 5 from base roadmap
- FastAPI backend exposing your data
- Next.js + Leaflet frontend
- This is where it becomes a thing you'd actually show someone

### Ongoing — The continuous improvement loop
- Trip log discipline (every outing, no exceptions)
- Weekly retrains of the habitat model
- Quarterly re-evaluation of which features matter

---

## Section 10 — Specific Prompts for Specific Tasks

Save these. They're the kinds of prompts that produce good results.

### "I want to add feature X from the planning docs"

> Read `docs/planning/<filename.md>` Section <N>. I want to implement this. Before writing any code, propose a design that fits the existing architecture (refer to CLAUDE.md for conventions and current structure). Write me the plan as a checklist of files to create/modify. Don't implement yet.

### "I want to refactor something"

> The current code in `src/X.py` is messy because <reason>. Without changing behavior, refactor it to <specific structure>. Run `make test` after to confirm nothing broke. If tests fail, fix the issues, don't change the test expectations.

### "Something's broken and I don't know why"

> When I run `<exact command>`, I get `<exact error message>`. Don't guess — read the relevant source files, reproduce the issue, then identify the root cause. Propose a fix, but don't implement until I approve.

### "I want to learn how this works"

> Walk me through how `<file or module>` works. Use the actual code as your reference. Explain it like I'm a competent programmer who hasn't seen this code before. Highlight any patterns I should know about.

### "Let me brainstorm before coding"

> I'm thinking about adding <feature>. I'm not sure if it belongs in this project yet. Before any planning, push back: ask me questions about what problem this actually solves, whether it fits the project's exploration-first framing, and what simpler alternatives exist. Don't be agreeable.

---

## Section 11 — Things to Avoid (Hard-Won Lessons)

1. **Don't skip plan mode for "small" features.** What feels small often touches more than you think.
2. **Don't let Claude run wild on file deletions or schema changes.** These are the highest-risk operations. Approve them one at a time.
3. **Don't commit `.env` or API keys.** Add to `.gitignore` immediately. If you do, rotate the keys; assume they're compromised.
4. **Don't merge data ingestion features without caching.** You'll burn rate limits and money. Every external API call gets cached.
5. **Don't let CLAUDE.md grow unbounded.** When it crosses ~150 lines, split via `@imports` to separate files.
6. **Don't fix the same misunderstanding twice without updating CLAUDE.md.** The third time is on you.
7. **Don't write features faster than you write tests for them.** "I'll add tests later" = no tests, ever.
8. **Don't build Phase 5 (map UI) before Phases 1–4 are solid.** It's tempting because UI is visible progress, but a beautiful map on shallow data is worse than ugly CLI on real data.

---

## Section 12 — Your First Five Prompts (TL;DR)

If you skipped everything above, here are the prompts to run in order:

1. *(in `claude`)* "Read every file in `docs/planning/`. Summarize what we're building and Phase 0/1 requirements."
2. *(plan mode)* "Set up Phase 0 from the roadmap — uv, Python 3.11, the folder structure, Makefile, pyproject.toml with anthropic/httpx/sqlite-utils/pydantic/python-dotenv/rich/typer/pytest. Plan first."
3. "Create a CLAUDE.md under 100 lines for this project. Include tech stack, my fishing context (Toronto, Ontario; smallmouth/brook trout/pike/walleye; intermediate; stream + small lakes; exploration-focused), conventions, and pointers to docs/planning/."
4. *(plan mode)* "Implement Phase 1 — a CLI bot with chat/log/recent/profile commands, SQLite storage, Anthropic SDK integration, system prompt in prompts/system.md. Tests for storage. Plan first."
5. *(plan mode)* "Implement Phase 2 — iNaturalist ingestion with caching, persistence to fishing.db, and wire it as a tool the chat agent can call. Plan first."

After prompt 5, you have a bot that knows your gear, remembers your trips, and can answer "what species have been observed near me in the last month" with real data. That's a meaningful Day 1–7.

From there, each subsequent feature in the planning docs is the same loop: read the relevant section, plan mode, implement, verify, commit, update CLAUDE.md if conventions changed.

---

## The Meta-Lesson

Vibe coding rewards momentum more than perfection. The planning docs are scaffolding — they're not contracts. As you build, you'll discover which features matter for *your* fishing and which were just interesting on paper. The trip log will become your single most valuable dataset. The hydrological analysis will probably take longer than you expect and pay off more than you expect. The bot will get genuinely useful around the 4–6 week mark.

Don't try to build everything. Build the parts you'd actually use. Then build more.

Tight lines.
