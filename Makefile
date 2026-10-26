.PHONY: run serve app-install app-dev app-build app-test test lint format ingest weekly-ingest ingest-hydat ingest-trca harvest-ca recent build-features train-sdm compute-access compute-untapped build-stretches build-anchors build-spots export-map

run:
	uv run python -m src.cli.main run

# Web app + API on this machine, against the local database (data/fishing.db).
# Open http://localhost:8000/app. Bound to localhost only; pass SERVE_HOST=0.0.0.0 to
# reach it from a phone on the same network.
SERVE_HOST ?= 127.0.0.1
SERVE_PORT ?= 8000
serve:
	uv run python -m src.cli.main serve --host $(SERVE_HOST) --port $(SERVE_PORT)

# The Apple-style web app in app/ (Vite + React). Needs Node 20+ and npm.
#   make app-install   once, fetch the JavaScript dependencies
#   make app-dev       dev server on http://localhost:5173, talking to `make serve`
#   make app-build     static build into app/dist, ready for any static host
#   make app-test      the app's unit tests
# Set VITE_API_BASE at build time to point the build at a separately hosted API
# (see app/.env.example); leave it unset to call the page's own origin.
app-install:
	cd app && npm install

app-dev:
	cd app && npm run dev

app-build:
	cd app && npm run build

app-test:
	cd app && npm test

test:
	uv run pytest tests/

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

ingest:
	uv run python -m src.cli.main ingest

# Every area in data/ingest_areas.json, run in sequence against the local
# database. Replaces the old weekly GitHub Action. Preview without fetching:
#   uv run python -m src.cli.main weekly-ingest --dry-run
# Schedule it weekly with cron (Sundays 2am local time; cron's PATH is short,
# so add uv's directory if `which uv` is not /usr/bin or /bin):
#   0 2 * * 0  cd /path/to/fishbot && PATH=$HOME/.local/bin:$PATH make weekly-ingest >> data/weekly_ingest.log 2>&1
weekly-ingest:
	uv run python -m src.cli.main weekly-ingest

ingest-hydat:
	uv run python -m src.cli.main ingest-hydat

# Conservation Authority fish community surveys — abundance and real absences.
ingest-trca:
	uv run python -m src.cli.main ingest-trca

# Discovery only, writes no records. Plain HTTP and no model calls, so this is
# free to run on a schedule; the report names any authority that publishes fish
# data without an adapter yet. Safe for cron:
#   0 4 * * 1  cd /path/to/fishbot && make harvest-ca
harvest-ca:
	uv run python -m src.cli.main harvest-ca

recent:
	uv run python -m src.cli.main recent

build-features:
	uv run python -m src.cli.main build-features

train-sdm:
	uv run python -m src.cli.main train-sdm

compute-access:
	uv run python -m src.cli.main compute-access

compute-untapped:
	uv run python -m src.cli.main compute-untapped

# Explore map, Level 1: named fishing stretches from data/curation/. Fetches the
# OHN network for the curation file's build area (cached 30 days), so the first
# run needs the network; later runs are offline until the cache expires.
# Level 2 anchors (confluences, dams, lake mouths, access points) are rebuilt as
# the second half of build-stretches, because rebuilding stretches clears them.
# build-anchors redoes only that half, e.g. to refresh OpenStreetMap data.
build-stretches:
	uv run python -m src.cli.main build-stretches

build-anchors:
	uv run python -m src.cli.main build-anchors

# Spots are derived from the stored anchors; build-anchors already runs this at the end.
build-spots:
	uv run python -m src.cli.main build-spots

export-map:
	uv run python -m src.cli.export_map
	@echo "Open data/processed/map_index.html in your browser."
