# Coverage check

`coverage-check` measures the map against authoritative reference layers for any region
and exits non-zero when a hard gate fails.

```
uv run python -m src.cli.main coverage-check --jurisdiction CA-ON --focus home --db data/fishing.db
```

- **Build mode** (default): walks every reference feature down the cause ladder against the
  build database (not near curated water, no anchor, no spot) and checks the listing table.
  Run before every reseed.
- **Host mode**: `--hosted-db <copy of the host's file>` or `--api https://… --api-key …`
  adds deploy parity and the deploy rung. `--api` also checks `/health` from outside vantage
  points (check-host.net) and reads the host's tables through `GET /admin/coverage-probe`.
- Reports: JSON and markdown in `--out-dir` (default `data/coverage/reports`).
- Exit codes: 0 no hard gate failed, 1 a hard gate failed, 2 the check could not run.

Hard gates: API reachable, deploy parity, listing table populated. Focus gates (per-class
in-scope recall) inform but do not block. The listed-species leak count needs cards and is
reported as not measured.

## Adding a region

Copy `data/coverage/references_ca_on.toml` to `references_<code>.toml` (e.g. `us_mi`), and
change the focus box, layer URLs, field names and radii. Nothing in `src/services/coverage/`
is region-specific. Any ArcGIS REST point layer works with the generic adapter
(`src/ingest/global/arcgis_reference.py`). Set `independent = false` if the layer is also a
map source.

Mouths, lakes, access and placement checks (S2b) are not built yet.
