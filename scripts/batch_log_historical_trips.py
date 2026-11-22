"""Batch-log 12 historical fishing trips into the parsed_trips table.

Calls log_trip() for each entry, which internally calls parse_trip_from_text()
and snaps to the nearest OHN segment. Trips whose location can't be resolved
are skipped with a notice.

Usage: uv run python scripts/batch_log_historical_trips.py
"""

import time

from src.services.trip_logger import log_trip
from src.storage.database import get_db

from personal_trip_data import load_personal_trip_data

HISTORICAL_TRIPS = load_personal_trip_data("historical_trips")

def main() -> None:
    db = get_db()
    logged = 0
    skipped = 0

    print(f"Logging {len(HISTORICAL_TRIPS)} historical trips...\n")

    for i, (label, text) in enumerate(HISTORICAL_TRIPS, 1):
        print(f"=== Trip {i}/{len(HISTORICAL_TRIPS)}: {label} ===")

        # Skip if already logged (idempotent re-runs)
        if "parsed_trips" in db.table_names():
            existing = list(db["parsed_trips"].rows_where(
                "raw_text = ?", [text], limit=1
            ))
            if existing:
                print(f"  already logged (trip_id={existing[0]['trip_id']}) — skipping\n")
                logged += 1
                continue

        result = log_trip(db, text, user_id=1)  # the captain's own history

        if result["trip_id"] is None:
            print(f"  SKIPPED — location unresolved: {result['confirmation']}\n")
            skipped += 1
        else:
            parsed = result["parsed"]
            caught = parsed.get("species_caught") or []
            observed = parsed.get("species_observed") or []
            productive = parsed.get("was_productive")
            method = parsed.get("location_method") or "claude_coords"
            confidence = parsed.get("location_confidence")
            conf_str = f" ({confidence:.2f})" if confidence is not None else ""

            print(f"  trip_id:     {result['trip_id']}")
            print(f"  caught:      {', '.join(caught) if caught else 'none'}")
            if observed and observed != caught:
                print(f"  observed:    {', '.join(observed)}")
            print(f"  productive:  {productive}")
            print(f"  location:    {method}{conf_str}")
            if result["segment_snapped"]:
                dist = int(parsed.get("distance_to_segment_m") or 0)
                print(f"  OHN snap:    {result['segment_name']} ({dist}m)")
            else:
                print("  OHN snap:    none within 5km")
            print(f"  insights:    {result['insights_generated']} generated")
            print()
            logged += 1

        # Nominatim is already rate-limited inside _nominatim_geocode (1s sleep),
        # but add a small buffer between trips to be safe.
        if i < len(HISTORICAL_TRIPS):
            time.sleep(0.5)

    print("=" * 50)
    print(f"Summary: {logged} logged, {skipped} skipped (location unresolved)")


if __name__ == "__main__":
    main()
