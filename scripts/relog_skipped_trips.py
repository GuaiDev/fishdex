"""Re-log the 5 trips that were previously skipped during historical import.

These trips had edge cases (multi-stop days, vague dates, lake locations) that
the old flat parsed_trips schema couldn't handle. The new sessions + stops schema
handles them cleanly.

Usage: uv run python scripts/relog_skipped_trips.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from personal_trip_data import load_personal_trip_data

from src.services.trip_logger import log_session
from src.services.trip_parser import parse_session_from_text
from src.storage.database import get_db

SKIPPED_TRIPS = load_personal_trip_data("skipped_trips")


def main(auto: bool = False) -> None:
    db = get_db()
    total_sessions = 0
    total_stops = 0

    for i, trip_text in enumerate(SKIPPED_TRIPS, 1):
        print(f"\n{'=' * 60}")
        print(f"Trip {i}/{len(SKIPPED_TRIPS)}")
        print(f"{'=' * 60}")
        print(f"Text: {trip_text.strip()[:120]}...")

        parsed = parse_session_from_text(trip_text.strip(), db, user_id=1)

        print("\nParsed JSON:")
        print(json.dumps(parsed, indent=2, default=str))

        if auto:
            log_it = True
        else:
            choice = input("\nLog this session? (yes/skip): ").strip().lower()
            log_it = choice == "yes"

        if not log_it:
            print("Skipped.")
            continue

        result = log_session(parsed, db, user_id=1)
        total_sessions += 1
        total_stops += result["stops_logged"]
        print(f"Logged: session #{result['session_id']}, {result['stops_logged']} stops.")

    print(f"\n{'=' * 60}")
    print(f"Done. {total_sessions} sessions logged, {total_stops} stops total.")


if __name__ == "__main__":
    main(auto="--auto" in sys.argv)
