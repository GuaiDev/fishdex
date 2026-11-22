"""Review-mode historical trip logger.

For each trip: parses it, prints the full JSON for human review, then waits for
Enter (log) or "skip" + Enter (discard). Nothing is inserted without explicit
confirmation.

Usage: uv run python scripts/batch_log_historical_trips_v2.py
"""

import json

from src.services.trip_logger import log_trip
from src.services.trip_parser import parse_trip_from_text
from src.storage.database import get_db

from personal_trip_data import load_personal_trip_data

TRIPS = load_personal_trip_data("historical_trips_v2")

def main() -> None:
    db = get_db()
    logged: list[int] = []
    skipped: list[int] = []

    print(f"Review-mode batch logger — {len(TRIPS)} trips\n")
    print("Press Enter to LOG each trip, or type 'skip' + Enter to discard.\n")

    for i, trip_text in enumerate(TRIPS, 1):
        print(f"\n{'='*60}")
        print(f"TRIP {i} of {len(TRIPS)}")
        print(f"{'='*60}")
        print("INPUT TEXT:")
        print(trip_text.strip())
        print("\nParsing...")

        parsed = parse_trip_from_text(trip_text.strip(), db=db)

        if parsed.get("status") == "needs_location":
            print(f"\nLOCATION UNRESOLVED: {parsed['message']}")
            print("→ Skipping (log manually with more location detail)")
            skipped.append(i)
            continue

        print("\nPARSED RESULT:")
        print(json.dumps(parsed, indent=2, default=str))
        print("\nPress Enter to LOG, or type 'skip' + Enter to discard:")

        try:
            response = input().strip().lower()
        except EOFError:
            response = ""

        if response == "skip":
            print("→ Skipped")
            skipped.append(i)
        else:
            result = log_trip(db, trip_text.strip(), user_id=1)  # the captain's own history
            if result["trip_id"] is None:
                print(f"→ Log failed: {result['confirmation']}")
                skipped.append(i)
            else:
                print(f"→ Logged as trip_id={result['trip_id']}, "
                      f"snapped={result['segment_snapped']}, "
                      f"insights={result['insights_generated']}")
                logged.append(i)

    print(f"\n{'='*60}")
    print(f"Done. Logged: {len(logged)} trips {logged}")
    print(f"      Skipped: {len(skipped)} trips {skipped}")


if __name__ == "__main__":
    main()
