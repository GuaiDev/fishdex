# Hosting FishDex

Two pieces: the static app on **Vercel**, the FastAPI backend on **Fly.io** with the SQLite
database on a persistent volume. Visitors with no token get a read-only demo (the `/map/spots*` and `/map/sections*`
routes, limited to tier 1 spots in a capped area; no coach, no writes). Everything else needs a token
or invite code. Set `FISHBOT_PUBLIC_DEMO=0` (e.g. in `fly.toml` env) to close the demo.

## What goes where

| Piece | Host | Config |
|---|---|---|
| `app/` + what it loads from `design/lavish/` | Vercel | `vercel.json`, `scripts/build_vercel.sh` |
| API (`src/`, `prompts/`, `data/processed`, `data/curation`, `data/registry`) | Fly.io image | `Dockerfile`, `fly.toml`, `deploy/entrypoint.sh` |
| `fishing.db`, catch photos, live-call cache | Fly volume `/data` (`DATA_DIR`) | `fly.toml` `[mounts]` |

The Vercel build copies `app/` and `design/lavish/` into `dist/` and writes `dist/app/config.js`
with the API address (`FISHDEX_API_URL`, default `https://fishdex-api-jason.fly.dev`). The app uses that
as its default; the connect dialog still overrides it. `?api=` is accepted only for localhost or that same origin (see `app/README.md`).

`vercel.json` sets `"framework": null` on purpose. The repo has a FastAPI backend at its root, so
without it Vercel auto-detects FastAPI and tries to build the Python app, and the deploy fails. This
project is a static build (`buildCommand` + `outputDirectory`).

## Data: seed vs re-ingest

`data/` is about 1.8 GB locally, but most of it never needs to reach the host.

- **Baked into the image from git (small):** `data/processed` parquets, models and map JSON,
  `data/curation`, `data/registry`, `data/species_images` CSVs.
- **Shared seed imported onto the volume:** copy `data/fishing.db` to `/data/seed.db`,
  then run `fishbot import-shared /data/seed.db`. Corpus and map tables are replaced
  in one transaction; personal tables (including users, sessions, pins and catches)
  are preserved. Never upload a replacement over `/data/fishing.db`. The target
  schema is initialized by deployment; migrate both databases before importing.
- **Re-ingested or refetched on demand (do not upload):** `data/cache/` (API response cache,
  rebuilt as the app calls out; it lives on the volume so it survives restarts) and `data/raw/`
  (source downloads, only needed to re-run ingest).

## Captain checklist

1. Install the CLIs: `curl -L https://fly.io/install.sh | sh` and `npm i -g vercel`.
2. `fly auth login`
3. From the repo root: `fly launch --no-deploy --copy-config --name fishdex-api-jason` (change the name
   in `fly.toml` if taken; keep it as `app =`).
4. `fly volumes create fishdex_data --size 3 --region yyz`
5. Set secrets (you pick the values; use `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   for the key): `fly secrets set ANTHROPIC_API_KEY=... FISHBOT_API_KEY=...`
6. `fly deploy`
7. Copy the shared seed with `fly ssh sftp shell` → `put data/fishing.db /data/seed.db`.
   Import with `fly ssh console -C "python -m src.cli.main import-shared /data/seed.db"`.
   Review the printed before/after row counts. The import leaves personal tables intact
   and rolls back on any failure. Never put a seed at `/data/fishing.db`.
8. Check `https://<app>.fly.dev/health/live` returns `{"status":"ok"}` and
   `https://<app>.fly.dev/health/ready` returns `{"status":"ready", ...}`.
9. Make your token on the host: `fly ssh console -C "python -m src.cli.main token"`.
   For friends, create invite codes with `POST /admin/invite` using that token.
10. `vercel login`, then from the repo root `vercel link` and `vercel --prod`. In the Vercel
    project settings set `FISHDEX_API_URL=https://<app>.fly.dev` before the deploy if the Fly name
    differs from `fishdex-api-jason`.
11. Put the final Vercel address in `fly.toml` `FISHBOT_CORS_ORIGINS` (default
    `https://fishdex-red.vercel.app`) and `fly deploy` again. Until it matches, browsers block the app
    from calling the API.

12. Open the Vercel URL: it should load the demo with no sign-in. Click "Sign in", paste the token, and try the map and the coach.

Optional spend switch: set `FISHBOT_DAILY_MODEL_CALLS` to a positive integer (for example
`fly secrets set FISHBOT_DAILY_MODEL_CALLS=300`) to cap the server-wide daily budget in
[reservation units](../README.md#running-locally-and-hosting), rather than individual
model calls. A request that fits its account budget but exceeds the remaining server budget
returns 503 without charging the account. Unset, invalid, zero, or negative values disable
the server-wide cap; the account budget still applies.

## Health checks and request logs

`GET /health/live` reports process liveness without checking data. Fly uses
`GET /health/ready`, which returns 200 with `status: ready` only when the database
opens, its schema version matches the application, the map checks below pass, and Ontario has at least one row in each of `spots`,
`water_bodies`, `species_ranges` and `stream_segments`.
Otherwise it returns 503 with `status: not_ready` and individual `checks` results.
The legacy `/health` endpoint remains available but does not verify readiness.

API request logs are JSON on stdout, including when started with `fishbot serve`.
Each `http_request` record includes `request_id`, `route`, `status`, `latency_ms`,
and `user_hash` (a truncated SHA-256 of the authenticated user ID, or null).
Requests reaching the observability middleware receive an `X-Request-ID` response
header: a supplied nonempty ASCII value using only characters `!` through `~`
and at most 128 characters is
reused after trimming whitespace; otherwise the API generates a UUID.

## Refreshing the map data on the volume

Readiness reports the baked artifact's SHA-256 as `map_artifact_digest`.
The `map_artifact_consistent` check requires a nonempty `map_segments` table with
exactly the artifact's declared segment count. It does not detect changed contents
in a same-count artifact. Handle those changes by reseeding the database using
the procedure below, with map segments rebuilt from the new artifact. The digest
identifies the baked file, not the loaded database contents.

Before uploading a seed for a changed `data/processed/map_data.json`, rebuild its
`map_segments` from the matching artifact locally. Stop any local API using the
seed and back it up first. Clear only the local seed's map segments:

```sh
uv run python -c 'from src.storage.database import get_db; db = get_db(); db.execute("DELETE FROM map_segments"); db.conn.commit(); db.conn.close()'
```

Start the local API once to populate the empty table from the artifact, then stop it.
Deploy the matching artifact and repeat step 7: upload to `/data/seed.db` and run
`import-shared`. Confirm `/health/ready` succeeds. Same-count content changes also
require an import: the artifact digest identifies the baked file, not loaded rows.

After `make build-anchors` rebuilds the anchors and spots locally (live OpenStreetMap,
slow and cached afterwards), repeat step 7 with that seed. Check with
`GET /map/spots?tier=3&kind=below_dam` that the new below-dam spots are present. The
public demo only serves tier 1 spots; tier 2 or 3 requires sign-in.

## Notes

- `auto_stop_machines` is on, so an idle API sleeps and the first request takes a few seconds.
  Set `min_machines_running = 1` in `fly.toml` to keep it warm.
- SQLite means one machine only. Do not scale to more than one.
- For [ingest behavior and local commands](../README.md#setting-fishbot_api_key-locally),
  see the README. Run regional refreshes on the host with
  `fly ssh console -C "python -m src.cli.main weekly-ingest"`; use
  `fly ssh console -C "python -m src.cli.main ingest"` for source ingestion.
- Back up the volume with `fly volumes snapshots list fishdex_data` (daily by default).
