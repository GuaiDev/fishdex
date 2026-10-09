.PHONY: run serve test lint format ingest weekly-ingest ingest-hydat ingest-trca harvest-ca recent build-features train-sdm compute-access compute-untapped export-map

run:
	uv run python -m src.cli.main run

# Web app + API on this machine, against the local database (data/fishing.db).
# Open http://localhost:8000/app. Bound to localhost only; pass SERVE_HOST=0.0.0.0 to
# reach it from a phone on the same network.
SERVE_HOST ?= 127.0.0.1
SERVE_PORT ?= 8000
serve:
	uv run python -m src.cli.main serve --host $(SERVE_HOST) --port $(SERVE_PORT)

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

export-map:
	uv run python -m src.cli.export_map
	@echo "Open data/processed/map_index.html in your browser."
