"""Review-mode historical trip logger.

For each trip: parses it, prints the full JSON for human review, then waits for
Enter (log) or "skip" + Enter (discard). Nothing is inserted without explicit
confirmation.

Usage: uv run python scripts/historical/batch_log_historical_trips_v2.py
"""

import json

from src.services.trip_logger import log_trip
from src.services.trip_parser import parse_trip_from_text
from src.storage.database import get_db

TRIPS = [
    # 1
    """
    June 8 2025, evening. Osprey Marsh in Mississauga.
    Feeder fishing with packbait and corn. I caught 3 small carp around 2-3 lbs.
    My friend caught 2 bigger carp at 6-7 lbs. Productive session.
    """,

    # 2
    """
    Late May 2025 (approximate). Two stormwater ponds in Milton.
    Targeting carp with corn on the hook. Caught nothing at either pond.
    One pond was too weedy, kept snagging my line, couldn't present the bait.
    Unproductive.
    """,

    # 3
    """
    May 15 2025. Byng Island in Dunnville on the Grand River.
    Used sucker cutbait targeting channel catfish.
    Fished from 5:30pm to 10:30pm. Caught 2 small channel catfish. Slow night.
    """,

    # 4
    """
    May 10 2025. Started at a stormwater pond in Milton.
    Caught one small carp on corn. Then went to Osprey Marsh in Mississauga,
    fished a few hours, caught nothing — carp may have been spawning.
    Then tried Lake Aquitaine with a trout magnet for panfish, caught nothing.
    """,

    # 5
    """
    April 27 2025. Greenway Dog Park on the Thames River in London Ontario.
    Feeder fishing with corn and packbait for carp and buffalo carp.
    Fished a few hours. I caught nothing. My friend caught one 11 lb common carp.
    Bite died after noon. Getting consistent small taps but unsure when to strike —
    buffalo bite lighter than carp.
    """,

    # 6
    """
    April 26 2025. North London Athletic Fields on the Thames River in London Ontario
    (known locally as the poop chute).
    Worm fishing targeting suckers. Lots of bites all day.
    Caught: several shorthead redhorse, one 6 lb silver redhorse, one 7 lb common carp.
    Very productive.
    """,

    # 7
    """
    April 23 2025. The Forks in London Ontario — confluence of the north and south
    branches of the Thames River. Worm fishing with 3 people.
    Caught: many shorthead redhorse, many golden redhorse, two 8 lb silver redhorse,
    one 6 lb silver redhorse, small common carp at the end of the day.
    Very productive.
    """,

    # 8
    """
    April 19 2025. Snake Creek in London Ontario. Small creek fishing.
    Catching a common shiner or creek chub on almost every cast.
    Also caught one bluegill and one green sunfish.
    """,

    # 9
    """
    April 17 2025. The Forks in London Ontario.
    Extremely high and flooded conditions — originally planned to fish Greenway
    Dog Park but it was too flooded. Went to The Forks instead.
    Caught one shorthead redhorse and one 8 lb silver redhorse. Productive despite
    the high water.
    """,

    # 10
    """
    April 13 2025. Greenway Dog Park on the Thames River in London Ontario.
    Missed many bites throughout the day.
    Caught one small golden redhorse near the end of the session.
    """,

    # 11
    """
    March 2025, multiple sessions in London Ontario.
    Tried several times for suckers using worms.
    Also tried for walleye using white grubs with chartreuse tails.
    Did not catch anything in any of these sessions.
    """,

    # 12
    """
    December 2024 or February 2025. Downtown Oakville pier.
    Cast spoons targeting trout and pike. Caught nothing.
    Then went to Burloak Boat Club harbour and drilled holes to ice fish
    with mealworms. Not a single bite. Completely unproductive.
    """,
]


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
            result = log_trip(db, trip_text.strip())
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
