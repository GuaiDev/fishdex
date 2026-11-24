"""Table ownership and the only supported shared-data replacement path.

Personal includes user-derived caches, credentials and operational ledgers. Unknown
names are never imported. FTS and spatial indexes are rebuilt by target triggers,
not copied from a seed. Schema migrations belong to the application, not the seed.
"""

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from sqlite_utils import Database

PERSONAL_TABLES = frozenset(
    """
trips recommendations behavioral_insights dismissed_segments sessions stops catches
personal_bests parsed_trips api_usage chat_sessions chat_messages angler_context
segment_synthesis tool_usage session_conditions users invite_codes user_sessions
daily_usage user_patterns pins pin_links pin_recalled_visits weed_reports llm_usage
ingest_runs schema_migrations sqlite_sequence questionnaire_answers
""".split()
)
CORPUS_TABLES = frozenset(
    """
observations gbif_observations stream_gauge_readings stocking_records species_ranges
reddit_posts regulation_chunks water_quality_readings bird_observations
stream_temperature_readings stream_temperature_summaries critical_habitat
tidal_readings salmon_escapement fish_surveys knowledge_sources knowledge_chunks
species_image shared_insights
""".split()
)
MAP_TABLES = frozenset(
    """
water_features access_points stream_segments barriers crown_land map_segments
fishing_stretches stretch_segments stretch_anchors stretch_segment_lines
stretch_osm_coverage spots water_bodies shoreline_features bathymetry_contours
hydro_segments fmz_boundaries place_species_counts
""".split()
)
SHARED_TABLES = CORPUS_TABLES | MAP_TABLES
# These are maintained by SQLite virtual tables and the target's own triggers.
DERIVED_TABLES = frozenset(
    f"{base}{suffix}"
    for base, suffixes in (
        ("reddit_posts_fts", ("", "_data", "_idx", "_docsize", "_config")),
        ("knowledge_chunks_fts", ("", "_data", "_idx", "_docsize", "_config")),
        ("stream_segments_rtree", ("", "_rowid", "_node", "_parent")),
        ("hydro_segments_rtree", ("", "_rowid", "_node", "_parent")),
    )
    for suffix in suffixes
)
TABLE_OWNERSHIP = {
    **dict.fromkeys(PERSONAL_TABLES, "personal"),
    **dict.fromkeys(CORPUS_TABLES, "corpus"),
    **dict.fromkeys(MAP_TABLES | DERIVED_TABLES, "map"),
}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def import_shared(
    source: Path,
    target: Path,
    *,
    tables: Iterable[str] | None = None,
    report: Callable[[str], None] = print,
) -> dict[str, tuple[int, int]]:
    """Replace allowlisted tables present in the seed, atomically.

    Both databases must already exist with compatible schemas. Missing seed
    tables leave target tables alone; empty seed tables deliberately clear them.
    Source schemas, triggers, users and migration records are never installed.
    The authorizer also refuses indirect personal writes from target triggers.
    """
    source, target = Path(source).resolve(strict=True), Path(target).resolve(strict=True)
    if source.samefile(target):
        raise ValueError("Source and target must be different database files")
    selected = set(tables) if tables is not None else SHARED_TABLES
    forbidden = selected - SHARED_TABLES
    if forbidden:
        raise ValueError(f"Refusing non-shared tables: {', '.join(sorted(forbidden))}")
    conn = sqlite3.connect(f"{target.as_uri()}?mode=rw", uri=True, timeout=5)
    try:
        conn.execute("ATTACH DATABASE ? AS seed", (f"{source.as_uri()}?mode=ro",))
        conn.execute("BEGIN IMMEDIATE")
        available = {
            r[0] for r in conn.execute("SELECT name FROM seed.sqlite_master WHERE type='table'")
        }
        selected = sorted(selected & available)
        if not selected:
            raise ValueError("Source contains no shared tables")
        counts = {}
        columns = {}
        for table in selected:
            quoted = _quote(table)
            source_cols = [r[1] for r in conn.execute(f"PRAGMA seed.table_info({quoted})")]
            target_cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({quoted})")]
            if set(source_cols) != set(target_cols):
                raise ValueError(f"Incompatible schema for {table}; migrate both databases first")
            columns[table] = ", ".join(map(_quote, target_cols))
            before = conn.execute(f"SELECT COUNT(*) FROM main.{quoted}").fetchone()[0]
            incoming = conn.execute(f"SELECT COUNT(*) FROM seed.{quoted}").fetchone()[0]
            counts[table] = (before, incoming)
            report(f"{table}: before={before} source={incoming}")

        def authorize(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                if database != "main" or table not in SHARED_TABLES | DERIVED_TABLES:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        for table in selected:
            quoted = _quote(table)
            conn.execute(f"DELETE FROM main.{quoted}")
        for table in selected:
            quoted = _quote(table)
            cols = columns[table]
            conn.execute(f"INSERT INTO main.{quoted} ({cols}) SELECT {cols} FROM seed.{quoted}")
        conn.commit()
        for table, (before, _) in counts.items():
            after = conn.execute(f"SELECT COUNT(*) FROM main.{_quote(table)}").fetchone()[0]
            counts[table] = (before, after)
            report(f"{table}: before={before} after={after}")
        return counts
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# Personal tables that belong to no single angler: operational ledgers and SQLite internals.
UNOWNED_PERSONAL_TABLES = frozenset({"ingest_runs", "schema_migrations", "sqlite_sequence"})
# Personal tables keyed by something other than a user_id column. {uid} is the angler's id.
_OWNER_PREDICATES = {
    "users": "id = :uid",
    "invite_codes": "used_by = :uid",
    "recommendations": "trip_id IN (SELECT id FROM trips WHERE user_id = :uid)",
}


def owner_predicates(db: Database) -> dict[str, str]:
    """Table -> WHERE clause (binding :uid) selecting one angler's rows, for every
    personal table that exists. A personal table that cannot be attributed to an
    angler raises, so a new table is classified here rather than silently kept
    after a delete."""
    predicates = {}
    for table in sorted(PERSONAL_TABLES - UNOWNED_PERSONAL_TABLES):
        if table not in db.table_names():
            continue
        if table in _OWNER_PREDICATES:
            predicates[table] = _OWNER_PREDICATES[table]
        elif "user_id" in {c.name for c in db[table].columns}:
            predicates[table] = "user_id = :uid"
        else:
            raise ValueError(f"Personal table {table} has no owner rule in ownership.py")
    return predicates


def count_user_rows(db: Database, user_id: int) -> dict[str, int]:
    """Rows each personal table holds for one angler (only tables with rows)."""
    counts = {}
    for table, where in owner_predicates(db).items():
        n = db.execute(
            f"SELECT COUNT(*) FROM {_quote(table)} WHERE {where}", {"uid": user_id}
        ).fetchone()[0]
        if n:
            counts[table] = n
    return counts


def user_photo_files(db: Database, user_id: int, photos_dir: Path) -> list[Path]:
    """Photo files on disk that this angler's catches and stops point at.

    Only paths that resolve inside photos_dir are returned, so a bad row can
    never aim a delete at an arbitrary file."""
    root = Path(photos_dir).resolve()
    names = set()
    for table, column in (
        ("catches", "photo_path"),
        ("catches", "photo_url"),
        ("stops", "photo_url"),
    ):
        if table not in db.table_names():
            continue
        for (value,) in db.execute(
            f"SELECT {column} FROM {table} WHERE user_id = ? AND {column} IS NOT NULL",
            [user_id],
        ):
            if value:
                names.add(Path(value).name)
    files = []
    for name in sorted(names):
        path = (root / name).resolve()
        if path.parent == root and path.is_file():
            files.append(path)
    return files


def delete_user_data(
    db: Database, user_id: int, photos_dir: Path, *, dry_run: bool = False
) -> dict:
    """Remove every personal row and photo file for one angler.

    Returns {"rows": {table: n}, "photos": n}. Rows go in one transaction; photo
    files are unlinked only after it commits. A dry run changes nothing."""
    predicates = owner_predicates(db)
    photos = user_photo_files(db, user_id, photos_dir)
    rows = count_user_rows(db, user_id)
    if dry_run:
        return {"rows": rows, "photos": len(photos)}
    with db.conn:
        # recommendations resolve through trips, so they must go before trips do.
        order = ["recommendations"] + [
            t for t in predicates if t not in ("recommendations", "users")
        ]
        for table in [*order, "users"]:
            if table in predicates:
                db.execute(
                    f"DELETE FROM {_quote(table)} WHERE {predicates[table]}", {"uid": user_id}
                )
    for path in photos:
        path.unlink(missing_ok=True)
    return {"rows": rows, "photos": len(photos)}


# Which tables make up each section of an account export.
EXPORT_SECTIONS = {
    "profile": ("users", "questionnaire_answers", "angler_context"),
    "sessions": ("sessions", "session_conditions"),
    "stops": ("stops",),
    "catches": ("catches", "personal_bests"),
    "pins": ("pins", "pin_links", "pin_recalled_visits"),
    "chat": ("chat_sessions", "chat_messages"),
}


def export_user_data(db: Database, user_id: int) -> dict:
    """Everything an angler logged, as plain JSON-ready dicts, with per-table counts."""
    predicates = owner_predicates(db)
    data, counts = {}, {}
    for section, tables in EXPORT_SECTIONS.items():
        data[section] = {}
        for table in tables:
            if table not in predicates:
                continue
            cursor = db.execute(
                f"SELECT * FROM {_quote(table)} WHERE {predicates[table]} ORDER BY rowid",
                {"uid": user_id},
            )
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, r)) for r in cursor.fetchall()]
            data[section][table] = rows
            counts[table] = len(rows)
    return {"user_id": user_id, "counts": counts, **data}
