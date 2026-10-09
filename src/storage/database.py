"""SQLite database setup for trips and future tables."""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlite_utils import Database

# Respect DATA_DIR env var for Railway persistent volumes; default to local data/
DB_PATH = Path(os.environ.get("DATA_DIR", "data")) / "fishing.db"

logger = logging.getLogger(__name__)


def get_db(path: Path | None = None) -> Database:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    db = Database(p)
    ensure_schema(db)
    return db



def _ensure_fish_surveys(db: Database) -> None:
    """Create the fish_surveys table. Idempotent.

    (station_name, visit_date) is the sampling-event key and is indexed as
    such: grouping by it is what turns a list of catches into an event with
    known effort, from which an absence can be read.
    """
    if "fish_surveys" not in db.table_names():
        db.execute("""
            CREATE TABLE IF NOT EXISTS fish_surveys (
                record_id               TEXT PRIMARY KEY,
                survey_program          TEXT NOT NULL,
                station_name            TEXT NOT NULL,
                visit_date              TEXT,
                sample_year             INTEGER,
                watershed               TEXT,
                subwatershed            TEXT,
                lat                     REAL,
                lng                     REAL,
                jurisdiction            TEXT NOT NULL,
                species_common_name     TEXT NOT NULL,
                species_scientific_name TEXT,
                total_count             INTEGER,
                total_weight_g          REAL,
                sampling_protocol       TEXT,
                source_url              TEXT,
                ingested_at             TEXT
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fish_surveys_event "
            "ON fish_surveys(station_name, visit_date)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fish_surveys_coords ON fish_surveys(lat, lng)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fish_surveys_species "
            "ON fish_surveys(species_common_name)"
        )
        db.conn.commit()

def _ensure_fishing_stretches(db: Database) -> None:
    """Create the explore map's Level 1 tables. Idempotent.

    stretch_segments is keyed on ogf_id alone: an OHN segment belongs to at
    most one stretch. Two stretches drawn over the same water would show the
    map two overlapping lines and leave a later step not knowing which
    stretch a confluence on that water belongs to.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS fishing_stretches (
            stretch_id          TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            river               TEXT NOT NULL,
            region              TEXT,
            jurisdiction        TEXT NOT NULL,
            length_km           REAL NOT NULL,
            segment_count       INTEGER NOT NULL,
            geometry_geojson    TEXT NOT NULL,
            bbox_min_lng        REAL NOT NULL,
            bbox_min_lat        REAL NOT NULL,
            bbox_max_lng        REAL NOT NULL,
            bbox_max_lat        REAL NOT NULL,
            centroid_lat        REAL NOT NULL,
            centroid_lng        REAL NOT NULL,
            sort_order          INTEGER NOT NULL DEFAULT 0,
            notes               TEXT,
            source              TEXT NOT NULL,
            built_at            TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS stretch_segments (
            ogf_id              INTEGER PRIMARY KEY,
            stretch_id          TEXT NOT NULL REFERENCES fishing_stretches(stretch_id),
            jurisdiction        TEXT NOT NULL,
            seq                 INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_fishing_stretches_jurisdiction "
        "ON fishing_stretches(jurisdiction)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_stretch_segments_stretch ON stretch_segments(stretch_id)"
    )
    db.conn.commit()


def _apply_migrations(db: Database) -> None:
    """Bring an existing schema up to current. Every step is idempotent.

    Called from ensure_schema so that "the schema exists" and "the schema is
    current" are the same statement. They used to differ: ensure_schema created
    tables while the migrations lived in get_db(), so anything constructing a
    Database directly got a schema with no user_id on stops/sessions — which is
    precisely why a cross-user data leak in coaching.py was invisible to nine
    test files at once.
    """
    migrate_catches_species_confirmation(db)
    migrate_catches_biggest_size_cm(db)
    migrate_catches_caught_at(db)
    migrate_behavioral_insights(db)
    migrate_stops(db)
    migrate_angler_context_multi_user(db)
    migrate_user_fields(db)
    migrate_stream_segments_multi_jurisdiction(db)
    migrate_observations_source(db)
    migrate_observations_licensing(db)
    migrate_gbif_licensing(db)
    migrate_gbif_survey_provenance(db)
    _ensure_fish_surveys(db)
    _ensure_fishing_stretches(db)
    migrate_species_status_provenance(db)
    migrate_regulation_chunks_zone_name(db)
    migrate_segment_synthesis_jurisdiction(db)
    migrate_user_patterns(db)


def migrate_user_patterns(db: Database) -> None:
    """Create the derived-user-layer cache. Idempotent.

    Nothing is backfilled and nothing needs to be: a miss recomputes. The
    table holds no facts of its own, only a cached derivation of the stops
    table, so dropping it costs time and never answers.
    """
    from src.storage.user_patterns import ensure_table

    ensure_table(db)


def migrate_stops(db: Database) -> None:
    """Add columns to stops table. Idempotent."""
    new_columns = [
        ("party_species_caught", "TEXT"),
        ("time_of_day", "TEXT"),
        ("hour_of_day", "INTEGER"),
        ("photo_lat", "REAL"),
        ("photo_lng", "REAL"),
        ("photo_taken_at", "TEXT"),
        ("photo_url", "TEXT"),
    ]
    for col_name, col_type in new_columns:
        try:
            db.execute(f"ALTER TABLE stops ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # column already exists


def migrate_catches_species_confirmation(db: Database) -> None:
    """Add species-confirmation columns to catches. Idempotent.

    species_confirmed gates whether a catch's species has been reviewed by
    the user — text-parsed and photo-suggested species are never committed
    as fact without this going forward. suggested_species preserves what was
    suggested (source + confidence) alongside what the user actually
    confirmed, for future model fine-tuning.

    Catches that already existed before this migration ran were logged under
    the old always-commit behavior and the user already accepted them —
    they're grandfathered to confirmed=1 once, here, rather than retroactively
    vanishing from FishDex the moment this migration lands.
    """
    if "species_confirmed" in {c.name for c in db["catches"].columns}:
        return  # already migrated
    new_columns = [
        ("species_confirmed", "INTEGER DEFAULT 0"),
        ("suggested_species", "TEXT"),
        ("confirmed_at", "TEXT"),
    ]
    for col_name, col_type in new_columns:
        try:
            db.execute(f"ALTER TABLE catches ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass  # column already exists
    db.execute("UPDATE catches SET species_confirmed = 1 WHERE species_confirmed = 0")


def migrate_catches_biggest_size_cm(db: Database) -> None:
    """Add the structured biggest_size_cm column to catches. Idempotent.

    The multi-catch logging UI's size field lands here, normalized to
    centimeters (see trip_logger.parse_size_to_cm), separate from the
    legacy free-text biggest_size column that no earlier input path ever
    populated — keeps the two from colliding on format (numbers vs. "14 in").
    """
    if "biggest_size_cm" in {c.name for c in db["catches"].columns}:
        return  # already migrated
    try:
        db.execute("ALTER TABLE catches ADD COLUMN biggest_size_cm REAL")
    except Exception:
        pass  # column already exists


def migrate_catches_caught_at(db: Database) -> None:
    """Add the fast-tally logging UI's per-catch timestamp column to
    catches. Idempotent.

    Distinct from created_at: created_at is fixed to whenever the whole
    session's INSERT transaction runs at submit time (there is no
    incremental/live persistence — see log_session), so every catch in a
    multi-hour live session would otherwise share virtually the same
    created_at. caught_at carries the actual tap time from the client.
    """
    if "caught_at" in {c.name for c in db["catches"].columns}:
        return  # already migrated
    try:
        db.execute("ALTER TABLE catches ADD COLUMN caught_at TEXT")
    except Exception:
        pass  # column already exists


def migrate_behavioral_insights(db: Database) -> None:
    """Add location and recommendation fields to behavioral_insights.

    Safe to run multiple times — skips columns that already exist.
    """
    new_columns = [
        ("lat", "REAL"),
        ("lng", "REAL"),
        ("recommendation", "TEXT"),
        ("condition_season", "TEXT"),
        ("location_name", "TEXT"),
    ]
    for col_name, col_type in new_columns:
        try:
            db.execute(f"ALTER TABLE behavioral_insights ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass


def ensure_schema(db: Database) -> None:
    if "trips" not in db.table_names():
        db["trips"].create(
            {
                "id": int,
                "status": str,
                "date": str,
                "planned_for": str,
                "jurisdiction": str,
                "location_name": str,
                "lat": float,
                "lng": float,
                "species_caught": str,
                "conditions": str,
                "gear_used": str,
                "notes": str,
                "what_worked": str,
                "what_didnt": str,
                "created_at": str,
                "updated_at": str,
            },
            pk="id",
        )

    if "observations" not in db.table_names():
        db["observations"].create(
            {
                "observation_id": int,
                "species": str,
                "common_name": str,
                "taxon_id": int,
                "lat": float,
                "lng": float,
                "observed_on": str,
                "quality_grade": str,
                "photo_url": str,
                "observer": str,
                "place_guess": str,
                "jurisdiction": str,
                "ingested_at": str,
                "geoprivacy": str,
                "is_obscured": int,
                "obscuration_radius_km": float,
            },
            pk="observation_id",
        )
    else:
        # Migrate existing observations table — add geoprivacy columns if absent
        obs_cols = {c.name for c in db["observations"].columns}
        if "geoprivacy" not in obs_cols:
            db["observations"].add_column("geoprivacy", str, not_null_default="open")
        if "is_obscured" not in obs_cols:
            db["observations"].add_column("is_obscured", int, not_null_default=0)
        if "obscuration_radius_km" not in obs_cols:
            db["observations"].add_column("obscuration_radius_km", float)

    if "recommendations" not in db.table_names():
        db["recommendations"].create(
            {
                "id": int,
                "timestamp": str,
                "species": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "conditions_json": str,
                "recommendation_json": str,
                "was_used": int,
                "trip_id": int,
            },
            pk="id",
        )

    if "gbif_observations" not in db.table_names():
        db["gbif_observations"].create(
            {
                "gbif_key": int,
                "species": str,
                "common_name": str,
                "taxon_key": int,
                "lat": float,
                "lng": float,
                "observed_on": str,
                "country_code": str,
                "dataset_name": str,
                "basis_of_record": str,
                "coordinate_uncertainty_m": float,
                "jurisdiction": str,
                "ingested_at": str,
            },
            pk="gbif_key",
        )

    if "behavioral_insights" not in db.table_names():
        db["behavioral_insights"].create(
            {
                "id": int,
                "species": str,
                "condition_type": str,
                "condition_context": str,
                "conclusion": str,
                "confidence": str,
                "source_type": str,
                "source_detail": str,
                "evidence_count": int,
                "version": int,
                "is_current": int,
                "contradicted_by": int,
                "user_verified": int,
                "jurisdiction": str,
                "lat": float,
                "lng": float,
                "recommendation": str,
                "condition_season": str,
                "location_name": str,
                "last_validated": str,
                "created_at": str,
            },
            pk="id",
        )

    if "water_features" not in db.table_names():
        db["water_features"].create(
            {
                "osm_id": str,
                "feature_type": str,
                "name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "area_m2": float,
                "tags": str,
                "fetched_at": str,
            },
            pk="osm_id",
        )

    if "access_points" not in db.table_names():
        db["access_points"].create(
            {
                "osm_id": str,
                "access_type": str,
                "name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "tags": str,
                "fetched_at": str,
            },
            pk="osm_id",
        )

    if "stream_gauge_readings" not in db.table_names():
        db["stream_gauge_readings"].create(
            {
                "id": int,
                "station_id": str,
                "station_name": str,
                "river_name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "water_level_m": float,
                "discharge_cms": float,
                "level_trend": str,
                "discharge_trend": str,
                "level_grade": str,
                "reading_datetime": str,
                "fetched_at": str,
            },
            pk="id",
        )
        db["stream_gauge_readings"].create_index(
            ["station_id", "reading_datetime"], unique=True, if_not_exists=True
        )

    if "stocking_records" not in db.table_names():
        db["stocking_records"].create(
            {
                "record_id": str,
                "waterbody_name": str,
                "waterbody_code": str,
                "municipality": str,
                "county": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "species": str,
                "species_code": str,
                "year": int,
                "month": int,
                "quantity": int,
                "life_stage": str,
                "stocking_purpose": str,
                "stocked_at": str,
            },
            pk="record_id",
        )

    if "species_ranges" not in db.table_names():
        db["species_ranges"].create(
            {
                "species": str,
                "scientific_name": str,
                "native_to_ontario": int,
                "native_to_great_lakes": int,
                "introduced": int,
                "extirpated_from_ontario": int,
                "general_range": str,
                "habitat_notes": str,
                "jurisdictions_present": str,  # JSON array
                "sara_status": str,
                "ontario_status": str,
                "cosewic_status": str,
                "fishing_notes": str,
                "last_updated": str,
            },
            pk="species",
        )

    if "reddit_posts" not in db.table_names():
        db["reddit_posts"].create(
            {
                "post_id": str,
                "subreddit": str,
                "post_type": str,
                "title": str,
                "body": str,
                "url": str,
                "author": str,
                "score": int,
                "num_comments": int,
                "parent_post_id": str,
                "created_utc": str,
                "extracted_species": str,  # JSON array
                "extracted_locations": str,  # JSON array
                "jurisdiction": str,
                "ingested_at": str,
            },
            pk="post_id",
        )

    if "reddit_posts_fts" not in db.table_names():
        db["reddit_posts"].enable_fts(["title", "body"], create_triggers=True)
        db["reddit_posts"].populate_fts(["title", "body"])

    if "stream_segments" not in db.table_names():
        db["stream_segments"].create(
            {
                "ogf_id": int,
                "watercourse_type": str,
                "name": str,
                "flow_verified": int,  # 0 or 1
                "permanency": str,
                "flow_classification": str,
                "length_m": float,
                "geom_wkt": str,
                "start_node": str,
                "end_node": str,
                "jurisdiction": str,
                "ingested_at": str,
            },
            pk="ogf_id",
        )
        db["stream_segments"].create_index(["name"], if_not_exists=True)
        db["stream_segments"].create_index(["start_node"], if_not_exists=True)
        db["stream_segments"].create_index(["end_node"], if_not_exists=True)

    if "barriers" not in db.table_names():
        db["barriers"].create(
            {
                "ogf_id": int,
                "barrier_type": str,
                "geom_wkt": str,
                "nearest_segment_ogf_id": int,
                "snap_distance_m": float,
                "jurisdiction": str,
                "ingested_at": str,
            },
            pk="ogf_id",
        )

    if "regulation_chunks" not in db.table_names():
        db["regulation_chunks"].create(
            {
                "zone": int,
                # "" for a zone chunk; the section name for province-wide rules,
                # which use zone 0. Part of the key so several can coexist.
                "section": str,
                "jurisdiction": str,
                "regulation_year": int,
                "raw_text": str,
                "char_count": int,
                "source_url": str,
                "ingested_at": str,
            },
            pk=["zone", "section", "jurisdiction", "regulation_year"],
        )

    if "water_quality_readings" not in db.table_names():
        db["water_quality_readings"].create(
            {
                "record_id": str,
                "station_id": str,
                "station_name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "sampled_at": str,
                "do_mgl": float,
                "ph": float,
                "temp_c": float,
                "conductivity_us_cm": float,
                "turbidity_fnu": float,
            },
            pk="record_id",
        )
        db["water_quality_readings"].create_index(["station_id"], if_not_exists=True)
        db["water_quality_readings"].create_index(["sampled_at"], if_not_exists=True)

    if "bird_observations" not in db.table_names():
        db["bird_observations"].create(
            {
                "obs_id": str,
                "species_code": str,
                "common_name": str,
                "scientific_name": str,
                "lat": float,
                "lng": float,
                "observed_on": str,
                "how_many": int,
                "location_name": str,
                "jurisdiction": str,
                "piscivore_significance": str,
                "fetched_at": str,
            },
            pk="obs_id",
        )
        db["bird_observations"].create_index(["species_code"], if_not_exists=True)
        db["bird_observations"].create_index(["observed_on"], if_not_exists=True)

    if "stream_temperature_readings" not in db.table_names():
        db["stream_temperature_readings"].create(
            {
                "station_id": str,
                "station_name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "year": int,
                "month": int,
                "mean_temp_c": float,
                "max_temp_c": float,
                "min_temp_c": float,
                "days_measured": int,
            },
            pk=["station_id", "year", "month"],
        )

    if "stream_temperature_summaries" not in db.table_names():
        db["stream_temperature_summaries"].create(
            {
                "station_id": str,
                "station_name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "summer_mean_c": float,
                "summer_max_c": float,
                "thermal_regime": str,
                "years_of_data": int,
                "species_notes": str,
            },
            pk="station_id",
        )

    if "crown_land" not in db.table_names():
        db["crown_land"].create(
            {
                "crown_id": str,
                "land_use_type": str,
                "geom_wkt": str,
                "centroid_lat": float,
                "centroid_lng": float,
                "bbox_minx": float,
                "bbox_miny": float,
                "bbox_maxx": float,
                "bbox_maxy": float,
                "fetched_at": str,
            },
            pk="crown_id",
        )

    # No sdm_predictions table. Nothing writes it since train-sdm stopped
    # persisting predictions, and nothing has read it since the exploration
    # path that rendered model output as "Recorded nearby" was deleted.
    # Creating it on every fresh clone would advertise a store that does not
    # exist. Existing databases keep their empty copy; it is inert.

    if "dismissed_segments" not in db.table_names():
        db["dismissed_segments"].create(
            {
                "ogf_id": int,
                "dismissed_at": str,
                "reason": str,
            },
            pk="ogf_id",
        )

    if "critical_habitat" not in db.table_names():
        db["critical_habitat"].create(
            {
                "habitat_id": str,
                "species_name": str,
                "species_common_name": str,
                "habitat_type": str,
                "jurisdiction": str,
                "geom_centroid_lat": float,
                "geom_centroid_lng": float,
                "sara_status": str,
                "source": str,
                "ingested_at": str,
            },
            pk="habitat_id",
        )
        db["critical_habitat"].create_index(["jurisdiction"], if_not_exists=True)
        db["critical_habitat"].create_index(
            ["geom_centroid_lat", "geom_centroid_lng"], if_not_exists=True
        )

    if "tidal_readings" not in db.table_names():
        db["tidal_readings"].create(
            {
                "record_id": str,
                "station_id": str,
                "station_name": str,
                "lat": float,
                "lng": float,
                "jurisdiction": str,
                "prediction_datetime": str,
                "water_level_m": float,
                "data_type": str,
                "tide_type": str,
                "fetched_at": str,
            },
            pk="record_id",
        )
        db["tidal_readings"].create_index(["station_id"], if_not_exists=True)
        db["tidal_readings"].create_index(["prediction_datetime"], if_not_exists=True)

    if "salmon_escapement" not in db.table_names():
        db["salmon_escapement"].create(
            {
                "record_id": str,
                "population_id": str,
                "waterbody_name": str,
                "gazetted_name": str,
                "watershed_code": str,
                "species": str,
                "analysis_year": int,
                "max_estimate": int,
                "stream_lat": float,
                "stream_lng": float,
                "jurisdiction": str,
                "source": str,
                "ingested_at": str,
            },
            pk="record_id",
        )
        db["salmon_escapement"].create_index(["species"], if_not_exists=True)
        db["salmon_escapement"].create_index(["waterbody_name"], if_not_exists=True)

    if "sessions" not in db.table_names():
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT,
                date_approx TEXT,
                overall_notes TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

    if "stops" not in db.table_names():
        db.execute("""
            CREATE TABLE IF NOT EXISTS stops (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                location_text       TEXT NOT NULL,
                location_name       TEXT,
                lat                 REAL,
                lng                 REAL,
                ohn_segment_id      TEXT,
                location_method     TEXT,
                location_confidence REAL,
                species_caught      TEXT,
                party_species_caught TEXT,
                was_productive      INTEGER,
                technique           TEXT,
                gear                TEXT,
                water_level         TEXT,
                water_clarity       TEXT,
                water_temp_c        REAL,
                weather_notes       TEXT,
                notes               TEXT,
                time_of_day         TEXT,
                hour_of_day         INTEGER,
                photo_lat           REAL,
                photo_lng           REAL,
                photo_taken_at      TEXT,
                photo_url           TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            )
        """)

    if "catches" not in db.table_names():
        db.execute("""
            CREATE TABLE IF NOT EXISTS catches (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                stop_id             INTEGER NOT NULL REFERENCES stops(id) ON DELETE CASCADE,
                session_id          INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                user_id             INTEGER NOT NULL,
                species             TEXT NOT NULL,
                count               INTEGER,
                biggest_size        TEXT,
                biggest_size_cm     REAL,
                bait                TEXT,
                photo_path          TEXT,
                photo_url           TEXT,
                photo_lat           REAL,
                photo_lng           REAL,
                photo_taken_at      TEXT,
                caught_at           TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_catches_stop ON catches(stop_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catches_session ON catches(session_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_catches_user ON catches(user_id)")

    if "personal_bests" not in db.table_names():
        # One row per (user, species) — the current best confirmed-or-not
        # size on record, updated at catch-insert time by
        # trip_logger._maybe_update_personal_best whenever a structured
        # catch (from the multi-catch logging UI) carries a size. Distinct
        # from computing "best of all catches" on every /fishdex read: this
        # is the explicit, persisted fact, with an UNIQUE constraint so
        # ON CONFLICT upserts have a target index to hit.
        db.execute("""
            CREATE TABLE IF NOT EXISTS personal_bests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                species         TEXT NOT NULL,
                best_size_cm    REAL NOT NULL,
                catch_id        INTEGER NOT NULL REFERENCES catches(id) ON DELETE CASCADE,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, species)
            )
        """)

    if "parsed_trips" not in db.table_names():
        db["parsed_trips"].create(
            {
                "trip_id": int,
                "user_id": str,
                "logged_at": str,
                "trip_date": str,
                "location_description": str,
                "waterbody_name": str,
                "lat": float,
                "lng": float,
                "ogf_id": int,
                "distance_to_segment_m": float,
                "species_caught": str,  # JSON array
                "species_observed": str,  # JSON array
                "species_targeted": str,
                "water_level": str,
                "water_clarity": str,
                "water_temp_c": float,
                "weather": str,
                "flow_trend": str,
                "habitat_notes": str,
                "spot_type": str,
                "fish_count": int,
                "was_productive": int,  # 0/1/null
                "gear": str,
                "notes": str,
                "raw_text": str,
                "location_method": str,
                "location_confidence": float,
            },
            pk="trip_id",
        )
        db["parsed_trips"].create_index(["ogf_id"], if_not_exists=True)
        db["parsed_trips"].create_index(["trip_date"], if_not_exists=True)
    else:
        # Migrate: add location resolution columns if absent
        pt_cols = {c.name for c in db["parsed_trips"].columns}
        for col, col_type in [("location_method", "TEXT"), ("location_confidence", "REAL")]:
            if col not in pt_cols:
                try:
                    db.execute(f"ALTER TABLE parsed_trips ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

    if "knowledge_sources" not in db.table_names():
        db["knowledge_sources"].create(
            {
                "id": int,
                "source_type": str,
                "source_id": str,
                "title": str,
                "url": str,
                "channel_name": str,
                "published_at": str,
                "search_query": str,
                "transcript_raw": str,
                "ingested_at": str,
            },
            pk="id",
        )
        db["knowledge_sources"].create_index(
            ["source_type", "source_id"], unique=True, if_not_exists=True
        )

    if "knowledge_chunks" not in db.table_names():
        db["knowledge_chunks"].create(
            {
                "id": int,
                "source_id": int,
                "chunk_index": int,
                "chunk_text": str,
                "created_at": str,
            },
            pk="id",
            foreign_keys=[("source_id", "knowledge_sources", "id")],
        )
        db["knowledge_chunks"].create_index(["source_id"], if_not_exists=True)
        db["knowledge_chunks"].enable_fts(["chunk_text"], create_triggers=True)

    db.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,
            timestamp       TEXT DEFAULT (datetime('now')),
            model           TEXT,
            input_tokens    INTEGER,
            output_tokens   INTEGER,
            total_tokens    INTEGER,
            tool_calls_made INTEGER DEFAULT 0,
            endpoint        TEXT DEFAULT 'chat'
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id      TEXT PRIMARY KEY,
            started_at      TEXT DEFAULT (datetime('now')),
            ended_at        TEXT,
            turn_count      INTEGER DEFAULT 0,
            summary         TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            turn_index      INTEGER NOT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_session
            ON chat_messages(session_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS angler_context (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL DEFAULT 1,
            content         TEXT NOT NULL DEFAULT '',
            last_updated    TEXT DEFAULT (datetime('now')),
            session_count   INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_angler_context_user
            ON angler_context(user_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS segment_synthesis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key       TEXT UNIQUE NOT NULL,
            lat             REAL,
            lng             REAL,
            location_name   TEXT,
            jurisdiction    TEXT,
            synthesis       TEXT NOT NULL,
            data_sources    TEXT,
            computed_at     TEXT DEFAULT (datetime('now')),
            hit_count       INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_segment_synthesis_key
            ON segment_synthesis(cache_key)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_segment_synthesis_loc
            ON segment_synthesis(lat, lng)
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tool_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,
            timestamp       TEXT DEFAULT (datetime('now')),
            tool_name       TEXT NOT NULL,
            input_summary   TEXT,
            success         INTEGER DEFAULT 1
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tool_usage_session ON tool_usage(session_id)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tool_usage_tool ON tool_usage(tool_name)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS session_conditions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              INTEGER NOT NULL UNIQUE,
            enriched_at             TEXT DEFAULT (datetime('now')),
            enrichment_source       TEXT,

            air_temp_c              REAL,
            feels_like_c            REAL,
            pressure_hpa            REAL,
            precip_mm               REAL,
            cloud_cover_pct         INTEGER,
            wind_speed_kmh          REAL,
            wind_direction_deg      INTEGER,
            weather_code            INTEGER,

            water_temp_c            REAL,
            do_mgl                  REAL,
            ph                      REAL,
            conductivity_us_cm      REAL,
            turbidity_fnu           REAL,
            pwqmn_station_name      TEXT,
            pwqmn_station_dist_km   REAL,
            pwqmn_sampled_at        TEXT,

            water_temp_anomaly_c    REAL,
            air_temp_anomaly_c      REAL,
            anomaly_flag            TEXT,

            days_since_rain         INTEGER,
            moon_phase              REAL,
            season                  TEXT,
            time_of_day             TEXT,

            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_conditions_session
            ON session_conditions(session_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            username            TEXT UNIQUE NOT NULL,
            display_name        TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            last_seen_at        TEXT,
            is_active           INTEGER DEFAULT 1,
            daily_message_limit INTEGER DEFAULT 50,
            role                TEXT DEFAULT 'beta'
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            created_by  INTEGER,
            used_by     INTEGER,
            created_at  TEXT DEFAULT (datetime('now')),
            used_at     TEXT,
            is_active   INTEGER DEFAULT 1,
            note        TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            token       TEXT UNIQUE NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            expires_at  TEXT,
            last_used_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS daily_usage (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            date            TEXT NOT NULL,
            message_count   INTEGER DEFAULT 0,
            UNIQUE(user_id, date)
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(token)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, date)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS map_segments (
            ogf_id              INTEGER PRIMARY KEY,
            lat                 REAL NOT NULL,
            lng                 REAL NOT NULL,
            score_balanced      REAL,
            score_easy          REAL,
            score_adventure     REAL,
            habitat_score       REAL,
            access_score        REAL,
            stream_order        INTEGER,
            watercourse_name    TEXT,
            nearest_named_stream TEXT,
            is_confluence       INTEGER DEFAULT 0,
            connected_to_waterbody INTEGER DEFAULT 0,
            observation_pressure REAL,
            top1_species        TEXT,
            top1_prob           REAL,
            top2_species        TEXT,
            top2_prob           REAL,
            google_maps_url     TEXT,
            swoop_url           TEXT
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_map_segments_lat_lng
            ON map_segments(lat, lng)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_map_segments_score_balanced
            ON map_segments(score_balanced DESC)
    """)

    _apply_migrations(db)


def migrate_angler_context_multi_user(db: Database) -> None:
    """Convert singleton angler_context to per-user table. Idempotent."""
    if "angler_context" not in db.table_names():
        return
    cols = {c.name for c in db["angler_context"].columns}
    if "user_id" in cols:
        return  # Already migrated

    try:
        existing_row = db["angler_context"].get(1)
        existing_content = existing_row.get("content", "") if existing_row else ""
        existing_session_count = existing_row.get("session_count", 0) if existing_row else 0
        existing_last_updated = existing_row.get("last_updated") if existing_row else None
    except Exception:
        existing_content = ""
        existing_session_count = 0
        existing_last_updated = None

    db.execute("ALTER TABLE angler_context RENAME TO angler_context_old")
    db.execute("""
        CREATE TABLE angler_context (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL DEFAULT 1,
            content         TEXT NOT NULL DEFAULT '',
            last_updated    TEXT DEFAULT (datetime('now')),
            session_count   INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_angler_context_user
            ON angler_context(user_id)
    """)
    if existing_content:
        db.execute(
            "INSERT INTO angler_context (user_id, content, last_updated, session_count) "
            "VALUES (1, ?, ?, ?)",
            [
                existing_content,
                existing_last_updated or datetime.now().isoformat(),
                existing_session_count,
            ],
        )
    db.execute("DROP TABLE IF EXISTS angler_context_old")
    db.conn.commit()


def migrate_user_fields(db: Database) -> None:
    """Add user_id to personal tables. Idempotent. Existing rows get user_id=1."""
    personal_tables = [
        "sessions",
        "stops",
        "behavioral_insights",
        "session_conditions",
        "chat_sessions",
        "chat_messages",
        "api_usage",
        "tool_usage",
    ]
    for table in personal_tables:
        if table not in db.table_names():
            continue
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass  # Column already exists
        try:
            db.execute(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL")
            db.conn.commit()
        except Exception:
            pass

    # Ensure Jason is user_id=1
    try:
        existing = list(db.execute("SELECT id FROM users WHERE id = 1").fetchall())
        if not existing:
            db.execute(
                "INSERT OR IGNORE INTO users (id, username, display_name, role) "
                "VALUES (1, 'jason', 'Jason', 'admin')"
            )
            db.conn.commit()
    except Exception:
        pass


def migrate_stream_segments_multi_jurisdiction(db: Database) -> None:
    """Add stream_order and segment_source columns to stream_segments. Idempotent.

    stream_order: Strahler order (NULL for existing OHN rows that predate this migration)
    segment_source: 'OHN' for Ontario Hydro Network, 'FWA' for BC Freshwater Atlas
    """
    if "stream_segments" not in db.table_names():
        return
    cols = {c.name for c in db["stream_segments"].columns}
    if "stream_order" not in cols:
        try:
            db.execute("ALTER TABLE stream_segments ADD COLUMN stream_order INTEGER")
        except Exception:
            pass
    if "segment_source" not in cols:
        try:
            db.execute("ALTER TABLE stream_segments ADD COLUMN segment_source TEXT DEFAULT 'OHN'")
            db.execute(
                "UPDATE stream_segments SET segment_source = 'OHN' WHERE segment_source IS NULL"
            )
            db.conn.commit()
        except Exception:
            pass


def migrate_observations_source(db: Database) -> None:
    """Add source column to observations table. Idempotent.

    Existing rows (all iNaturalist) receive source='iNaturalist'.
    """
    if "observations" not in db.table_names():
        return
    cols = {c.name for c in db["observations"].columns}
    if "source" not in cols:
        try:
            db.execute("ALTER TABLE observations ADD COLUMN source TEXT DEFAULT 'iNaturalist'")
            db.execute("UPDATE observations SET source = 'iNaturalist' WHERE source IS NULL")
            db.conn.commit()
        except Exception:
            pass


def migrate_observations_licensing(db: Database) -> None:
    """Add licence and attribution columns to observations. Idempotent.

    Existing rows are left NULL rather than defaulted: we genuinely do not know
    what licence they carry until they are backfilled from the cached API
    responses, and guessing would be worse than an honest gap.
    """
    if "observations" not in db.table_names():
        return
    cols = {c.name for c in db["observations"].columns}
    for name, coltype in (
        ("license_code", "TEXT"),
        ("photo_license_code", "TEXT"),
        ("observer_id", "INTEGER"),
        ("uri", "TEXT"),
    ):
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE observations ADD COLUMN {name} {coltype}")
            except Exception:
                pass
    db.conn.commit()


def migrate_species_status_provenance(db: Database) -> None:
    """Add status provenance to species_ranges. Idempotent.

    Left NULL for every existing row on purpose: none of the statuses in the
    committed species file were checked against a registry, so none may claim
    to have been. NULL is what makes the SAR check fail closed.
    """
    if "species_ranges" not in db.table_names():
        return

    # `status_verified_at` was stamped on every run of verify-species-status,
    # including a no-op re-application of the same export. That is honest as
    # "when did we last check" and dishonest as "when was this verified" — the
    # date moved on runs where nothing was confirmed anew. Renaming the column
    # is the fix; the behaviour was right and the name was not. Rename first so
    # the add-missing loop below does not create an empty second column beside
    # the populated one and silently orphan every existing citation.
    cols = {c.name for c in db["species_ranges"].columns}
    if "status_verified_at" in cols and "status_last_checked_at" not in cols:
        try:
            db.execute(
                "ALTER TABLE species_ranges "
                "RENAME COLUMN status_verified_at TO status_last_checked_at"
            )
            db.conn.commit()
            cols = {c.name for c in db["species_ranges"].columns}
        except Exception:  # noqa: BLE001 - older SQLite lacks RENAME COLUMN
            logger.warning(
                "Could not rename status_verified_at; leaving it in place",
                exc_info=True,
            )

    for name in (
        "status_source",
        "status_source_url",
        "status_last_checked_at",
        "status_assessed_on",
    ):
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE species_ranges ADD COLUMN {name} TEXT")
            except Exception:
                pass
    db.conn.commit()


def migrate_gbif_licensing(db: Database) -> None:
    """Add licence and attribution columns to gbif_observations. Idempotent.

    Left NULL for existing rows rather than defaulted — the licence is a fact
    about the publishing dataset, not something we may assume.
    """
    if "gbif_observations" not in db.table_names():
        return
    cols = {c.name for c in db["gbif_observations"].columns}
    for name, coltype in (
        ("license_code", "TEXT"),
        ("license_uri", "TEXT"),
        ("dataset_key", "TEXT"),
        ("rights_holder", "TEXT"),
        ("recorded_by", "TEXT"),
    ):
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE gbif_observations ADD COLUMN {name} {coltype}")
            except Exception:
                pass
    db.conn.commit()


def migrate_gbif_survey_provenance(db: Database) -> None:
    """Add survey-provenance columns to gbif_observations. Idempotent.

    These fields arrive from GBIF on gear-based records (electrofishing, seine,
    fyke net) and were previously discarded at parse, so a standardised survey
    record was stored indistinguishably from a casual photo. Left NULL for
    existing rows: absent is not the same as "no gear was used", and inventing
    a value here would be inventing the survey.

    Re-ingesting backfills them for records the adapter sees again.
    """
    if "gbif_observations" not in db.table_names():
        return
    cols = {c.name for c in db["gbif_observations"].columns}
    for name, coltype in (
        ("sampling_protocol", "TEXT"),
        ("event_id", "TEXT"),
        ("sampling_effort", "TEXT"),
        ("sample_size_value", "REAL"),
        ("sample_size_unit", "TEXT"),
    ):
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE gbif_observations ADD COLUMN {name} {coltype}")
            except Exception:
                pass
    db.conn.commit()


def migrate_regulation_chunks_zone_name(db: Database) -> None:
    """Add zone_name column to regulation_chunks for human-readable region names.

    Idempotent. BC uses region names ("Vancouver Island"), QC uses zone names.
    The existing zone INTEGER column remains the PK component; zone_name is metadata.
    """
    if "regulation_chunks" not in db.table_names():
        return
    cols = {c.name for c in db["regulation_chunks"].columns}
    if "zone_name" not in cols:
        try:
            db.execute("ALTER TABLE regulation_chunks ADD COLUMN zone_name TEXT")
            db.conn.commit()
        except Exception:
            pass


def migrate_segment_synthesis_jurisdiction(db: Database) -> None:
    """Add jurisdiction column to segment_synthesis for cross-jurisdiction cache
    collision safety — see src/services/synthesis_cache.py. Idempotent.
    """
    if "segment_synthesis" not in db.table_names():
        return
    cols = {c.name for c in db["segment_synthesis"].columns}
    if "jurisdiction" not in cols:
        try:
            db.execute("ALTER TABLE segment_synthesis ADD COLUMN jurisdiction TEXT")
            db.conn.commit()
        except Exception:
            pass


def cleanup_old_gauge_readings(db: Database, days: int = 7) -> None:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    db.execute("DELETE FROM stream_gauge_readings WHERE reading_datetime < ?", [cutoff])
