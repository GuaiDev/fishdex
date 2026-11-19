# Building Your Fishing Bot — Phase 1: The Data Brain

A beginner-friendly, step-by-step guide. No prior vibe coding experience assumed. By the end of this guide you'll have a working fishing AI bot running on your computer that knows about you, remembers your trips, and pulls real fishing data from the internet.

**Phase 1 covers:** the data synthesis bot we've been planning. The brain. Everything we've discussed across the planning docs gets built here.

**Phase 2 and beyond (not in this guide, but designed for):** gamification (XP, achievements, streaks), aquarium view of logged catches, multi-day trip planning, mobile app, map UI, voice mode. The architecture here is set up so these slot in later without a rewrite.

---

## Before You Start: What Vibe Coding Actually Is

Real quick, since this is your first time:

**Vibe coding** means you describe what you want in plain English to an AI assistant (Claude Code, in your case), and it writes the code for you. You review it, run it, tell it what's wrong, and iterate. You don't need to know how to write Python — but you do need to:

1. **Read what it produces** and roughly understand what's happening
2. **Test things** when it says it's done (don't trust, verify)
3. **Push back** when something feels off
4. **Commit your work often** to git (more on this below)

The AI is not magic. It will make mistakes. Your job is to catch them. The good news: it's much faster than typing every line yourself, and the mistakes are usually obvious if you actually run the code.

A few terms you'll hear:

- **Terminal** — the black-text-on-screen app where you type commands. On Mac it's called "Terminal"; on Windows you'll use one called "WSL" (we set this up below).
- **Repository / repo** — a folder that holds your project, with version control (git) tracking every change.
- **Commit** — saving a snapshot of your work to git. Like a save point in a video game.
- **Prompt** — what you type to the AI. The quality of your prompt determines the quality of what you get back.
- **Plan mode** — a special mode in Claude Code where it writes a plan first and waits for your approval before doing anything. Use this constantly.

That's the whole framework. Let's build.

---

## Part 1: Setup (≈45 minutes)

This part is annoying but you only do it once. Take your time and don't skip steps.

### 1.1 Get a Claude account

You need a paid Claude account to use Claude Code. The cheapest is **Claude Pro at $20/month** — that's more than enough for personal use.

1. Go to claude.com
2. Sign up or log in
3. Subscribe to Pro (you can cancel anytime)

### 1.2 Set up your terminal

**If you're on a Mac:**
- Open the app called "Terminal" (search for it in Spotlight by pressing Cmd+Space and typing "terminal")
- That's it, you're done with this step

**If you're on Windows:**
- Open PowerShell as Administrator (right-click the Windows menu → "Windows PowerShell (Admin)" or "Terminal (Admin)")
- Run this command: `wsl --install`
- Restart your computer when it tells you to
- After restart, search for "Ubuntu" in your start menu and open it
- It'll ask you to create a username and password — pick something simple, you'll need to type this password sometimes
- From now on, when this guide says "terminal," it means this Ubuntu window

**If you're on Linux:**
- You know what you're doing, open your terminal

### 1.3 Install git

In your terminal, run:

```bash
git --version
```

If it prints a version number, you're set. If it says "command not found":

- **Mac:** run `xcode-select --install` and click through the dialogs
- **Windows (in Ubuntu/WSL):** run `sudo apt update && sudo apt install -y git`
- **Linux:** install via your package manager

### 1.4 Install Python and uv

`uv` is a tool that manages Python and project dependencies for us. We use it because the alternatives (pip, conda, virtualenv) are more annoying.

In your terminal, run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then **close your terminal completely and reopen it.** This is important — `uv` won't work in the same window where you installed it.

Verify:

```bash
uv --version
```

You should see something like `uv 0.5.x`.

### 1.5 Install Claude Code

In your terminal:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Close and reopen your terminal again. Then:

```bash
claude --version
```

Should print a version. If it doesn't, ask Claude (the chat one, in your browser) for help with the specific error you're seeing.

### 1.6 Create your project folder

Pick a place on your computer where you keep code projects. If you don't have one, your home folder is fine. In terminal:

```bash
mkdir fishing-bot
cd fishing-bot
git init
```

What just happened:
- `mkdir fishing-bot` created a folder called fishing-bot
- `cd fishing-bot` moved you into it
- `git init` set up version control so you can save snapshots as you work

### 1.7 Copy in your planning docs

This is important. Your planning docs from the earlier conversation are what Claude Code will read to understand the project. Without them, you're starting from zero context.

```bash
mkdir -p docs/planning
```

Then copy your planning markdown files into the `docs/planning/` folder. The files you want:

- `fishing_ai_roadmap.md`
- `fishing_ai_advanced_capabilities.md`
- `fishing_ai_spot_discovery.md`
- `fishing_ai_feature_catalog.md`
- `fishing_ai_multi_jurisdiction.md`
- `fishing_ai_getting_started.md`
- `fishing_ai_build_guide.md`
- This file (`fishing_bot_phase1_beginner_guide.md`)

How to copy them depends on where you saved them. If they're in your Downloads folder on Mac:

```bash
cp ~/Downloads/fishing_ai_*.md docs/planning/
cp ~/Downloads/fishing_bot_phase1_beginner_guide.md docs/planning/
```

On Windows/WSL it's similar but you may need to navigate from `/mnt/c/Users/YourName/Downloads/`. Ask Claude Code if you get stuck — that's literally what it's for.

Verify they made it:

```bash
ls docs/planning/
```

You should see your `.md` files listed.

### Setup done. You only do all of that once.

---

## Part 2: How Claude Code Actually Works

Before you start using it, understand the basics. Five minutes here saves hours later.

### Launching it

```bash
claude
```

First time: it opens a browser window to log in. Authenticate with your Claude account. Future times: it just starts immediately in whatever folder you're in.

### The interaction model

You type a message → it responds → it may write code, run commands, read files, etc. → you respond → repeat. It's a chat, but the AI can actually do things on your computer (with limits — see "permissions" below).

### Plan mode — your most important habit

**Press `Shift+Tab` twice** to enter plan mode. When in plan mode, Claude Code writes out a plan of what it intends to do and waits for your approval before doing anything.

**Use plan mode for almost everything in this guide.** The cost of planning is near zero. The cost of bad code being written is real time wasted. When in doubt, plan mode.

To exit plan mode and let it execute the plan: type "approved" or "looks good, proceed."

To stay in plan mode but iterate on the plan: respond with corrections — "skip step 3, that's overkill" or "also add X to step 5."

### Other shortcuts you'll use

- `/clear` — wipe the conversation and start fresh. Use this when switching between unrelated tasks. Conversations get muddy when you mix topics.
- `/compact` — squish the conversation history to save context space. Use this when Claude Code tells you context is getting full.
- `/model sonnet` — switch to the default model (good for most work)
- `/model opus` — switch to the most powerful model (use for hard architectural decisions)
- `/model haiku` — switch to the cheapest, fastest model (use for bulk simple tasks)
- `Ctrl+C` — stop whatever Claude Code is doing right now. Use freely when you see it going off the rails.

### The CLAUDE.md file

In every project folder, Claude Code automatically reads a file called `CLAUDE.md` at the start of every session. This is the project's permanent memory. We're going to create one in Step 3.3 below. **This file is the single most important file in your project.** Treat it carefully.

### When it does something wrong

It will. Every vibe coder has stories. Recovery moves:

1. **Stop it** with Ctrl+C if it's mid-stream
2. **Tell it** specifically what went wrong: "That's not what I wanted. The issue is X. Try again with Y in mind."
3. **`/clear` and restart** if you've corrected the same misunderstanding twice. Fresh context is often faster than fighting a stuck assumption.
4. **`git restore .`** in your terminal to undo file changes since your last commit (this is why you commit often).

---

## Part 3: Phase 1 — Building the Data Brain

Now we actually build the thing.

### Strategy

Phase 1 has five sub-phases. Each ends with something that works on its own. Don't move to the next one until the current one is solid:

**1a. Setup the project skeleton** (1 hour)
**1b. Build the basic chat bot** (2-3 hours)
**1c. Add real fishing observation data** (2-3 hours)
**1d. Add weather and conditions** (1-2 hours)
**1e. Add a tactical recommender** (2-3 hours)

Realistic total: one full weekend or 3-4 evenings. At the end you'll have something genuinely useful that you'd open up before a fishing trip.

We're keeping Phase 2-feature seeds in mind throughout — the trip log we build is what feeds the aquarium later, the architecture is what supports trip planning later, etc.

### Sub-phase 1a: Project Skeleton

#### Step 1: Launch Claude Code in your project folder

In terminal, make sure you're in your project folder:

```bash
cd fishing-bot   # if you're not already here
claude
```

#### Step 2: Onboard Claude Code to the project

This is your very first prompt. **Copy and paste this exactly**:

> Hi. I'm building a personalized fishing AI bot for myself. This is my first time using Claude Code and I have no prior coding experience.
>
> Please read every file in `docs/planning/`. These are my planning documents. Then summarize back to me in plain English (no jargon):
>
> 1. What we're building, in one sentence
> 2. What Phase 1 covers and what's deferred to later phases (gamification, aquarium, trip planning all come later)
> 3. The key ethical rules I want enforced (especially: synthesis of public data is fine, reconstructing deliberately-hidden info is not)
> 4. The most important architectural rule: jurisdiction-aware design from Day 0
> 5. What we need to do first
>
> Don't write any code yet. After your summary, wait for me to confirm before doing anything.

Wait for it to respond. **Read the summary carefully.** This is your chance to catch any misunderstandings before they become baked into code.

If anything in the summary seems wrong or missing, tell it: "That's not quite right — point 3 should also mention X" or whatever the correction is.

When the summary looks right, say: "Confirmed. Let's proceed."

#### Step 3: Set up the project skeleton

Now press **Shift+Tab twice** to enter plan mode. Then paste:

> Set up the Phase 1 project skeleton based on the build guide. I want:
>
> 1. A folder structure that supports current Phase 1 needs AND future Phase 2 features (gamification, aquarium, trip planning). Use empty placeholder folders for future stuff with a README in each explaining what it'll hold.
>
> 2. Python 3.11+ via uv. Dependencies for now: anthropic, httpx, sqlite-utils, pydantic, python-dotenv, rich, typer, pytest, ruff.
>
> 3. A Makefile with simple commands: `make run`, `make test`, `make lint`, `make ingest`.
>
> 4. A `.gitignore` that excludes `.env`, the database, cache folders, and Python junk.
>
> 5. A `.env.example` file showing what environment variables I'll need (no real secrets).
>
> 6. Don't write any business logic yet — just the skeleton.
>
> Write me the plan as a numbered list of files to create. Wait for my approval before creating anything.

It will produce a plan. **Read it.** Things to check:
- Does it create folders for things you don't recognize? Ask "what's the X folder for?"
- Does it skip something obvious? Tell it.
- Does it propose adding dependencies not in your list? Ask "why?"

When you're happy with the plan, say "approved, proceed."

It'll create the files. When done, run this in a new terminal window (keep Claude Code running):

```bash
ls
```

You should see folders and files appear. Try:

```bash
make test
```

This should run with no errors (and zero tests, since we haven't written any). If you get errors, paste them back to Claude Code and let it fix.

#### Step 4: Create CLAUDE.md (the project's brain)

Still in Claude Code, paste this:

> Now create `CLAUDE.md` at the root of the project. This is the file you'll read every time we work together. Keep it under 100 lines. Include:
>
> **## Project**
> Use the current project status from [CLAUDE.md](../../CLAUDE.md#project); this historical setup guide does not own the release policy.
>
> **## About me**
> - Home: Toronto, Ontario (CA-ON)
> - Target species: smallmouth bass, brook trout, pike, walleye (update as needed)
> - Fishing style: stream + small lakes primarily
> - Skill level: intermediate
> - Top priority: exploration over optimization
> - Vibe coding experience: none (this is my first project) — explain things clearly, don't assume I know what jargon means
>
> **## Tech stack**
> Python 3.11+, uv, SQLite via sqlite-utils, Anthropic SDK with claude-sonnet-4-6, typer for CLI, pytest, ruff.
>
> **## Architecture rules**
> - Jurisdiction-aware from Day 0. Every location-bound record carries an ISO 3166-2 code (CA-ON, US-MI, etc.).
> - Data ingestion is layered: `src/ingest/global/` for sources that work anywhere, `src/ingest/jurisdictions/<code>/` for region-specific.
> - The agent talks to services (`src/services/`), never directly to ingest modules.
> - Every external API call goes through a cache. No exceptions.
> - Future Phase 2 features (gamification, aquarium, trip planner) get their own service modules — keep the door open but don't build now.
>
> **## Ethical rules (these are decided, do not relitigate)**
> - SYNTHESIS of public information is fine, including answering spot questions well from public forums, named locations in videos, government data, iNaturalist, etc.
> - RECONSTRUCTION of deliberately-hidden info is not. No vision pipelines that take obscured video and identify locations the creator hid.
> - No scraping platforms with active anti-scraping enforcement (Instagram, Facebook, TikTok, FishBrain, FishAngler).
> - Indigenous waters: flag as separate jurisdiction, don't predict within them.
> - YouTube transcript scraping is in a ToS grey zone but acceptable for personal use.
> - Tool output stays personal — don't add export features that broadcast spot lists.
>
> **## Conventions**
> - Always use `uv add` to install dependencies, never bare pip.
> - Use ruff for linting and formatting.
> - Tests in `tests/`, fixtures in `tests/fixtures/`.
> - Commits use conventional commit format (feat, fix, refactor, docs, test, chore).
> - When unsure, ask me in plain English. Don't assume.
>
> **## How to run things**
> - `make run` starts the chat bot
> - `make ingest` runs data ingestion
> - `make test` runs all tests
> - `make lint` checks code style
>
> **## Where to look**
> - Product spec: `docs/planning/` — read these before suggesting any feature
> - System prompt for the bot itself: `prompts/system.md`
>
> No fluff. Every line should be something I'd consult while coding.

It writes the file. **Read it before approving.** Edit by hand if you want — it's a plain markdown file.

#### Step 5: First commit

In Claude Code:

> Stage all changes and create the first git commit with the message "chore: initial project setup". Verify that `.env` is NOT staged (it shouldn't exist yet, but just in case).

It'll run the git commands. Verify in your terminal:

```bash
git log
```

You should see your first commit. **Congratulations, you have a real project.**

This is a good moment to take a break. Sub-phase 1a done.

---

### Sub-phase 1b: The Basic Chat Bot

Now we build the actual bot. By the end of this section you can have a real conversation with it, log trips, and see your history.

#### Step 6: Clear context (start fresh)

In Claude Code, type:

```
/clear
```

This wipes the conversation. We're starting a new task and don't want old context bleeding in.

#### Step 7: Reorient and plan the bot

Paste this:

> Read CLAUDE.md and `docs/planning/fishing_ai_build_guide.md` Phase 1 section. Then enter plan mode and propose how to implement the MVP fishing bot.
>
> What I want at the end of this step:
> - I can run `make run` and have a conversation with my fishing bot in the terminal
> - I can use a `log` command to record a trip (date, location, species caught, what worked, what didn't)
> - I can use a `recent` command to see my recent trips
> - I can use a `profile` command to view/edit my fishing profile
> - The bot uses my profile and recent trips as context for every conversation
>
> Critical design notes for Phase 2 future-proofing:
> - The trip model should include fields the aquarium will need later: species, size (length and weight), photo path (optional for now). Don't build the aquarium yet, but design the data shape so it slots in.
> - The trip model should also accommodate a future "trip plan" that becomes a real trip — include a status field (planned/completed) and a planned_for date.
> - Don't build gamification yet, but the trips table should have an `id` and timestamps so XP calculations later are easy.
>
> Critical design notes for the rest of Phase 1:
> - Jurisdiction-aware from Day 0 (use the ISO 3166-2 codes)
> - System prompt lives in `prompts/system.md` so I can edit it without touching code
> - Anthropic SDK with claude-sonnet-4-6 default
> - Tests for the storage layer
>
> Write me the plan as a checklist. Explain each file in plain English. Don't write code until I approve.

It writes a plan. **Read it carefully.** Things to push back on if you see them:

- It's adding a "trips_v2" table or some over-engineered thing → "Keep it simple, one trips table."
- It's adding dependencies not in CLAUDE.md → "Why do we need X? Can we skip it?"
- It's proposing complex configuration systems → "Just use a .env file, keep it simple."
- It's writing future-phase code now → "Don't build the aquarium logic, just leave the data shape compatible."

If you don't understand something in the plan, ASK. "What does 'pydantic model' mean? Explain it like I've never coded." Claude Code will explain. That's part of vibe coding.

When the plan looks right, say "approved, proceed."

#### Step 8: Watch it build

It'll start creating files. You'll see file paths scroll by. This is normal.

When it finishes, it'll probably say something like "I've implemented the MVP bot. Run `make run` to try it out."

**DO NOT TRUST. VERIFY.** Run this in your terminal:

```bash
make test
```

If tests pass, great. If they fail, copy-paste the entire error back to Claude Code:

> Tests failed with this output: [paste the error]. Please diagnose and fix.

Repeat until tests pass.

#### Step 9: Set up your Anthropic API key

The bot needs to talk to Claude. That requires an API key (different from your Claude Code login).

In Claude Code:

> I need to set up my Anthropic API key. Walk me through:
> 1. Where to get an API key (console.anthropic.com)
> 2. How to add it to the .env file
> 3. How to verify the bot can read it
> Explain each step in plain English.

Follow what it tells you. You'll go to console.anthropic.com, create an API key, add some credit ($5-10 is plenty to start), and paste the key into a `.env` file in your project.

**Make sure `.env` is in your `.gitignore`** — you do not want to commit your API key to git. Verify:

```bash
git status
```

`.env` should NOT appear in the list. If it does, tell Claude Code immediately.

#### Step 10: Set up your profile

```bash
make run
```

The bot should start. Use the profile command (it might be `/profile` or `profile` depending on what was built):

> Set up my profile:
> - Home: Toronto, Ontario
> - Target species: smallmouth bass, brook trout, pike, walleye
> - Style: stream and small lakes
> - Skill: intermediate
> - Gear: [add what you actually own]
> - Budget: [whatever feels right]

#### Step 11: Try a real conversation

Things to try:

- "Hi, what do you know about me?"
- "Recommend a good lure for smallmouth bass in stained water around 18°C."
- "What's the difference between fishing brook trout in streams vs lakes?"

Then log a fake trip:

> log

Walk through the prompts. Make up a fishing trip — date, location, what you caught.

Then test memory:

- "What did I catch on my last trip?"
- "Have I been to [the place you logged]?"

If anything feels generic, weak, or wrong, fix the system prompt. In Claude Code:

> The bot is being too generic. It said [paste what it said]. I want it more like [describe how a real fishing buddy would talk]. Update `prompts/system.md` to make it more opinionated, more specific, more like a knowledgeable friend.

#### Step 12: Commit

In Claude Code:

> Commit the current state with message "feat(phase-1a): MVP bot with chat, trips, profile". Run tests first to make sure everything still works.

Sub-phase 1b done. **You have a working personal fishing bot.** Take a break. Maybe go fishing. Log it for real when you come back.

---

### Sub-phase 1c: Real Fishing Observation Data

So far the bot is talking from its training. Now we wire it up to real, current data about what fish are actually being seen in your area.

We use **iNaturalist** — a citizen science platform where people log species observations worldwide. Their API is free, no key needed, and there's a ton of fish data.

#### Step 13: `/clear` and reorient

In Claude Code:

```
/clear
```

Then:

> Read CLAUDE.md. We've finished sub-phase 1b (basic chat bot working). Now starting sub-phase 1c: iNaturalist integration.
>
> Plan mode. I want:
> 1. A new module at `src/ingest/global/inaturalist.py` that fetches fish observations from iNaturalist for a given geographic area.
> 2. Caching so we don't hit their API repeatedly.
> 3. A database table for observations.
> 4. A `make ingest` command that pulls observations near my home location.
> 5. A new tool the chat bot can use to query these observations during conversation.
>
> Important: this is a "global" data source (works anywhere), not jurisdiction-specific.
>
> Tests with fake/mocked data, no real API hits in tests.
>
> Plan first. Explain each step plainly.

Review the plan. Approve. Let it build.

#### Step 14: Try the ingestion

When it's done:

```bash
make ingest
```

You should see it pulling data. After it finishes, in Claude Code, ask:

> Show me the top 10 fish species observed near my home in the last 6 months, based on what we just ingested.

If it returns real species names and counts, you're golden.

#### Step 15: Try it in the chat bot

```bash
make run
```

Try things like:
- "What fish have been observed near me in the last month?"
- "Has anyone reported brook trout near [a specific lake] recently?"
- "What's the most common species in my area?"

This is where the bot starts becoming meaningfully better than ChatGPT for your specific use case.

#### Step 16: Commit

> Commit with message "feat(phase-1c): iNaturalist observation ingestion and tool integration"

---

### Sub-phase 1d: Weather and Conditions

Fish behavior is driven by weather. We add real-time weather and forecasting.

#### Step 17: `/clear` and reorient

```
/clear
```

Then:

> Read CLAUDE.md. Sub-phase 1d: adding weather integration.
>
> Plan mode:
> 1. New module `src/ingest/global/weather.py` using Open-Meteo (free API, no key needed).
> 2. Functions for current conditions, forecast, and recent history (needed for barometric pressure trends — a huge fishing signal).
> 3. Cache responses (1hr for current, 6hr for forecast).
> 4. Wire as a tool the chat bot can call.
> 5. Add a derived "pressure trend" feature (falling/steady/rising over 24-48hr).
>
> Plan first. Tests with mocked data.

Approve, let it build, verify it works:

```bash
make run
```

> What are conditions like tomorrow morning at Lake Simcoe? Should I go fishing?

A good response pulls real forecast data and gives a real opinion grounded in fishing knowledge.

#### Step 18: Commit

> Commit "feat(phase-1d): weather and conditions integration"

---

### Sub-phase 1e: The Tactical Recommender

The daily-use feature. "What should I throw?"

#### Step 19: `/clear` and reorient

```
/clear
```

Then:

> Read CLAUDE.md. Sub-phase 1e: tactical recommendations.
>
> Plan mode:
> 1. New service `src/services/tactical.py` that recommends lures, colors, presentation, and depth based on species + conditions.
> 2. Rule-based first, no ML. Encode well-known fishing patterns (cold water = slow presentations, stained water = chartreuse/orange, etc.).
> 3. Add as a tool the chat bot can call, but also can be invoked proactively when I ask "what should I throw?"
> 4. The recommendations should be SAVED with the trip log when I use them, so we can later see what worked.
>
> Future Phase 2 thinking: the saved recommendations will eventually feed gamification ("3 catches with bot-recommended lures = achievement unlocked") — so make sure the link between recommendations and trip outcomes is preserved.
>
> Plan first. Tests with parametrized cases.

Approve and build.

#### Step 20: Try the full experience

```bash
make run
```

Have a full conversation:

> I'm fishing the Credit River tomorrow morning. Water's stained from last night's rain, air temp will be around 12°C, going for smallmouth. What should I throw?

A good response pulls real weather (Step 17), references iNaturalist observations of smallmouth in your area (Step 14), applies tactical rules (Step 19), and gives you 2-3 specific lure choices with reasoning.

**This is the milestone. This is a useful fishing bot.**

#### Step 21: Commit Phase 1 complete

> Commit "feat(phase-1): complete data brain — bot, trip log, iNaturalist, weather, tactical recommender"

---

## Part 4: Using It For Real

Now the boring-sounding but most-important part.

### The trip log is sacred

**Every time you go fishing, log it.** Every time. The bot's future capabilities depend entirely on the volume and quality of your trip data.

What to log (the bot will prompt you for this):
- Date, time you fished
- Location (lake/river name + GPS if possible)
- Conditions (the bot can fill these in from weather data — confirm/correct)
- What you caught (species + size if measured)
- What you used (rod, reel, line, lure/bait)
- What worked
- What didn't
- Anything notable (water clarity, what other anglers were doing, structure you noticed)

Three months of religious logging = a personalized fishing assistant. Three weeks of "I'll log later" = a generic chatbot.

### Iterating the system prompt

When the bot gives you a generic or weak answer, fix it. In Claude Code:

> The bot said [paste]. I wanted [describe]. Update `prompts/system.md` to fix this.

The system prompt is plain markdown. You can read it, edit it by hand, and tweak it as you go.

### Adding new data sources

When you read the planning docs and a feature sounds appealing, just ask:

> Read `docs/planning/fishing_ai_feature_catalog.md` Section 2 about weather features. I want to add barometric pressure trend analysis. Plan first.

Same loop as everything else.

### Cost watching

Run `/cost` periodically in Claude Code to see what you've spent. Phase 1 work should be ~$1-3 per evening session typically. If you're spending more:
- You're probably forgetting to `/clear` between unrelated tasks (huge context = expensive every turn)
- You're using `/model opus` when sonnet would do

---

## Part 5: What's Next (Phase 2 — Future)

When Phase 1 is solid and you've used it for a few weeks, you'll want to add the features you mentioned. Here's how each maps onto what we just built:

### Gamification

The trip log already has the data we need: catches, sizes, conditions, gear used. A future `src/services/gamification.py` will compute XP, streaks ("3 days in a row of fishing!"), achievements ("Caught 10 species this year!"), personal records, etc. Slots in cleanly on top of the existing trip table.

### Aquarium

Every logged catch with size data becomes a virtual fish in your collection. A future `src/services/aquarium.py` + a simple web UI will show your "biggest pike of 2026," "rarest species caught," etc. The photo path field we included in the trip model gets used here. No backend rewrite needed.

### Trip Planning

A "planned trip" is just a trip record with `status='planned'` and a `planned_for` date. A future `src/services/trip_planner.py` will let you say "plan me a weekend fishing trip for May targeting brook trout, max 3 hours drive" and have the bot synthesize iNaturalist data, weather forecasts, hydrology (when we add it in Phase 5 of the original plan), and your preferences into a real itinerary.

### The bigger phases from the planning docs

After gamification/aquarium/trip planning, you'd return to the original planning docs:
- Phase 4: Government regulatory data (Ontario MNRF, then expand)
- Phase 5: Hydrological network analysis (the big differentiator)
- Phase 6: Satellite-based spot discovery
- Phase 7: Habitat-based species prediction
- Phase 8: Map UI and voice mode

None of those require redoing Phase 1. They're all additive.

---

## Part 6: When You Get Stuck

Things will break. Here's the recovery playbook.

### "Claude Code did something I didn't want"

```bash
git status               # see what changed
git diff                 # see exactly what
git restore .            # undo all uncommitted changes
```

Then tell Claude Code what went wrong specifically.

### "I don't understand what it just did"

Just ask:

> Explain what you just did in plain English. Walk me through each file you changed and why.

### "The tests are failing and I don't know why"

> Tests are failing. Read the error output: [paste]. Diagnose the actual root cause, don't guess. Don't change the tests to make them pass — fix the code so tests pass for the right reason.

### "I'm three sessions deep and the bot's behavior is weird"

Sometimes context accumulates cruft. Try:

1. `/clear` and start fresh
2. Have it re-read CLAUDE.md
3. Describe the current weird behavior plainly
4. Ask it to diagnose with fresh eyes

### "I want to revert to an earlier working state"

```bash
git log                  # see all commits
git checkout <commit>    # try a specific older commit (read-only)
git checkout main        # back to current
```

If you want to permanently roll back:

> I want to revert to the commit "feat(phase-1c): iNaturalist...". Help me do this safely without losing any work I want to keep.

It'll walk you through.

### "I'm confused about what's happening"

You can always just ask:

> Pause. I'm confused. Walk me through the project structure, what we've built so far, what's in CLAUDE.md, and what we should do next. Treat me like I've forgotten everything.

---

## Part 7: The Habits That Make This Work

You'll hear these from every experienced vibe coder. They sound obvious. They are. People still skip them.

1. **Plan mode for non-trivial work.** `Shift+Tab` twice. Always.

2. **Verify, don't trust.** Every time Claude Code says "done," run the verification (tests, the actual command, look at the output).

3. **Commit often.** After every working unit, even small ones. Git is your undo button.

4. **`/clear` between unrelated tasks.** Context bleed = bad output.

5. **Update CLAUDE.md when patterns emerge.** Every time you correct the same mistake twice, add a line to CLAUDE.md to prevent the third time.

6. **Ask questions when confused.** "What does that mean?" "Why this approach?" "Show me an example." Claude Code will explain. That's the job.

7. **Push back when something feels off.** You're the product owner. Don't accept code you can't explain back to yourself.

8. **Log every trip.** I'm saying it again because it's the most important habit and the easiest to skip.

---

## Final Thought

You're about to spend a weekend building something that, when finished, is more useful to your fishing than any app on the market. Not because we're cleverer than the people who built FishAI — but because it's *yours*. It knows you. It learns from you. It only does what you want.

That's the whole pitch. The data brain we're building in Phase 1 is the foundation everything else sits on. Get this part right and the aquarium, gamification, trip planning, map UI — all the stuff you want next — slot in cleanly when you're ready.

Tight lines.
