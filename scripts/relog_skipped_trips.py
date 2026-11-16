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

from src.services.trip_logger import log_session
from src.services.trip_parser import parse_session_from_text
from src.storage.database import get_db

SKIPPED_TRIPS = [
    # Multi-stop day — 3 stops
    """
    May 10 2025. Started at a stormwater pond in Milton, caught one small common carp
    on corn. Then went to Osprey Marsh in Mississauga, fished a few hours, caught
    nothing — carp may have been spawning. Then tried Lake Aquitaine in Mississauga
    with a trout magnet for panfish, caught nothing.
    """,
    # Multi-stop day — went to one spot, moved to another
    """
    April 17 2025. Originally went to Greenway Dog Park on the Thames River in London
    Ontario but it was way too flooded to fish. Moved to The Forks in London Ontario
    instead. At The Forks caught one shorthead redhorse and one 8 lb silver redhorse
    despite the high water.
    """,
    # Vague multi-session
    """
    March 2025, multiple sessions in London Ontario. Tried several times for suckers
    using worms. Also tried for walleye using white grubs with chartreuse tails.
    Did not catch anything in any of these sessions.
    """,
    # Lake Ontario — no OHN segment
    """
    December 2024 or February 2025. Downtown Oakville pier on Lake Ontario.
    Cast spoons targeting trout and pike. Caught nothing.
    """,
    # Lake Ontario ice fishing
    """
    December 2024 or February 2025. Burloak Boat Club harbour on Lake Ontario,
    Oakville. Drilled holes and ice fished with mealworms. Not a single bite.
    Completely unproductive.
    """,
]


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
