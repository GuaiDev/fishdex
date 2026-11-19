"""Batch-log 12 historical fishing trips into the parsed_trips table.

Calls log_trip() for each entry, which internally calls parse_trip_from_text()
and snaps to the nearest OHN segment. Trips whose location can't be resolved
are skipped with a notice.

Usage: uv run python scripts/historical/batch_log_historical_trips.py
"""

import time

from src.services.trip_logger import log_trip
from src.storage.database import get_db

HISTORICAL_TRIPS = [
    # (label, text)
    (
        "Osprey Marsh carp — June 2025",
        "June 2025, fished Osprey Marsh near Dundas Street in Oakville. "
        "Caught three common carp sight-fishing along the edge, all around 5-8 lbs. "
        "Water was warm and clear, lots of carp cruising. Great session.",
    ),
    (
        "Milton stormwater ponds — May 2025",
        "May 2025, hit a few of the stormwater management ponds in Milton near Louis St Laurent Ave. "
        "Caught largemouth bass, yellow perch, and a couple of pumpkinseed sunfish. "
        "Water low and clear, sunny afternoon.",
    ),
    (
        "Byng Island — Grand River channel cats — May 15 2025",
        "May 15 2025, fished below Byng Island Conservation Area on the Grand River near Dunnville. "
        "Caught four channel catfish and one walleye, all on nightcrawlers near the current seam. "
        "Water normal, slightly stained, flow stable.",
    ),
    (
        "Sixteen Mile Creek Milton — May 10 2025",
        "May 10 2025, Sixteen Mile Creek in Milton near Derry Road. "
        "Caught creek chubs and white suckers in the riffle. Water low and clear.",
    ),
    (
        "Thames River Greenway carp — Apr 27 2025",
        "April 27 2025, fished the Thames River Greenway trail section in London near Wonderland Road. "
        "Caught two common carp and spotted a bowfin holding in the slack water. "
        "Water temperature around 12C, flow stable, clear conditions.",
    ),
    (
        "Thames River redhorse — Apr 26 2025",
        "April 26 2025, Thames River in London, caught a silver redhorse and two shorthead redhorse "
        "on the gravel spawning beds downstream of the Fanshawe weir area. "
        "Water clear, rising slightly. Got there at sunrise.",
    ),
    (
        "The Forks London redhorse — Apr 23 2025",
        "April 23 2025, The Forks of the Thames in London where the North and South branches meet. "
        "Caught three golden redhorse and one greater redhorse. "
        "Riffle habitat over gravel, water low and clear, perfect conditions.",
    ),
    (
        "Snake Creek shiners — Apr 19 2025",
        "April 19 2025, Snake Creek near its confluence with the Grand River south of Caledonia. "
        "Microfishing session — caught spotfin shiners, emerald shiners, and a brassy minnow. "
        "Small stream, order 2, clear water, stable flow.",
    ),
    (
        "The Forks flooded redhorse — Apr 17 2025",
        "April 17 2025, back at The Forks of the Thames in London. River flooded from recent rain, "
        "water turbid and high. Tried for two hours, zero fish. Completely blown out.",
    ),
    (
        "Greenway golden redhorse — Apr 13 2025",
        "April 13 2025, Thames River Greenway in London near Wharncliffe Road. "
        "Caught a beautiful golden redhorse, around 40cm. "
        "Water clear, flow stable, gravel/cobble riffle habitat. Solo session.",
    ),
    (
        "London suckers and walleye — Mar 2025",
        "March 2025, Thames River in London near the Springbank Park area. "
        "Spring run — caught three white suckers and surprisingly one walleye. "
        "Water cold, maybe 4C, flow high but fishable along the edges.",
    ),
    (
        "Oakville harbour — Dec 2024",
        "December 2024, fished the Sixteen Mile Creek mouth at Oakville harbour. "
        "Caught a small coho salmon and a brown trout on spoons. Cold and clear.",
    ),
    (
        "Burloak ice fishing — Feb 2025",
        "February 2025, ice fished near Burloak Waterfront Park on Lake Ontario. "
        "Caught yellow perch and one small lake trout jigging in 8 feet of water.",
    ),
]


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

        result = log_trip(db, text)

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
