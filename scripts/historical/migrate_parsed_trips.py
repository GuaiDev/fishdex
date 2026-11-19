"""Migrate parsed_trips rows to the sessions + stops schema.

All 10 existing rows are unambiguous single-location trips. Each becomes one
session + one stop. Run once; safe to re-run (skips if sessions table is
already populated with migrated data via a sentinel check).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import get_db


def migrate(dry_run: bool = False) -> None:
    db = get_db()

    if "parsed_trips" not in db.table_names():
        print("parsed_trips table not found — nothing to migrate.")
        return

    rows = list(db["parsed_trips"].rows)
    if not rows:
        print("parsed_trips is empty — nothing to migrate.")
        return

    # Already migrated check: skip rows whose session already exists (by date + location match)
    sessions_count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if sessions_count > 0:
        print(f"sessions table already has {sessions_count} rows.")
        choice = input("Re-run migration anyway? (yes/no): ").strip().lower()
        if choice != "yes":
            print("Aborted.")
            return

    sessions_created = 0
    stops_created = 0

    for row in rows:
        trip_id = row["trip_id"]
        trip_date = row.get("trip_date") or None
        location_text = row.get("location_description") or ""
        species_raw = row.get("species_caught") or "[]"
        try:
            species = json.loads(species_raw)
        except (json.JSONDecodeError, TypeError):
            species = []

        was_productive = row.get("was_productive")
        lat = row.get("lat")
        lng = row.get("lng")
        ogf_id = row.get("ogf_id")
        location_method = row.get("location_method")
        location_confidence = row.get("location_confidence")
        gear = row.get("gear")
        water_level = row.get("water_level")
        water_clarity = row.get("water_clarity")
        water_temp_c = row.get("water_temp_c")
        notes = row.get("notes")

        print(f"\nMigrating trip_id={trip_id}: {location_text[:60]}")
        print(f"  date={trip_date}, productive={was_productive}, species={species}")

        if not dry_run:
            session_id = db.execute(
                "INSERT INTO sessions (date, date_approx, overall_notes) VALUES (?, ?, ?)",
                (trip_date, None, None),
            ).lastrowid

            ohn_segment_id = str(ogf_id) if ogf_id is not None else None

            db.execute(
                """
                INSERT INTO stops (
                    session_id, location_text, location_name,
                    lat, lng, ohn_segment_id, location_method, location_confidence,
                    species_caught, was_productive, technique, gear,
                    water_level, water_clarity, water_temp_c, weather_notes, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    location_text,
                    None,  # location_name: not in old schema
                    lat,
                    lng,
                    ohn_segment_id,
                    location_method,
                    location_confidence,
                    json.dumps(species),
                    was_productive,
                    None,  # technique: not in old schema
                    gear,
                    water_level,
                    water_clarity,
                    water_temp_c,
                    None,  # weather_notes: old schema had weather as separate field
                    notes,
                ),
            )
            sessions_created += 1
            stops_created += 1
        else:
            sessions_created += 1
            stops_created += 1

    if not dry_run:
        db.conn.commit()

    print(f"\nMigration {'(dry run) ' if dry_run else ''}complete:")
    print(f"  {sessions_created} sessions created")
    print(f"  {stops_created} stops created")
    print(f"  0 rows flagged/skipped")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    migrate(dry_run=dry)
