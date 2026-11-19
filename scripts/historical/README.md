# Historical scripts

These are one-off scripts used to build the original operator's personal trip
history and to trial the community-ingest adapters. They are **not for beta
operators**: they write dated personal trips and profile context straight into
the database they are pointed at, and several assume the operator's own files
and coordinates. Do not run them against a beta database.

They are kept here rather than at the top level of `scripts/` so the beta
toolset stays obvious. Whether to delete them is a separate decision about the
personal data they contain; no such decision has been made.

- `batch_log_historical_trips.py`, `batch_log_historical_trips_v2.py`,
  `relog_skipped_trips.py` — bulk-log dated historical trips.
- `correct_angler_context.py`, `seed_angler_context.py` — seed or correct the
  operator's angler context.
- `enrich_historical_sessions.py` — retroactive session enrichment.
- `migrate_parsed_trips.py` — migrate parsed trip rows into the session schema.
- `test_youtube_ingest.py` — manual smoke test for the retired YouTube ingest
  path; it prints personal location queries and was never a pytest test.
