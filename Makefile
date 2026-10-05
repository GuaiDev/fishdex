.PHONY: run test lint format ingest ingest-hydat ingest-trca harvest-ca recent build-features train-sdm compute-access compute-untapped export-map

run:
	uv run python -m src.cli.main run

test:
	uv run pytest tests/

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

ingest:
	uv run python -m src.cli.main ingest

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
