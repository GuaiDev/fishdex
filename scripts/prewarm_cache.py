"""Pre-warm the synthesis cache for key fishing spots.

Runs one synthesis question per spot and stores the result so future
queries about those locations are served cheaply from cache.

Usage:
    uv run python scripts/prewarm_cache.py
    uv run python scripts/prewarm_cache.py --force   # clear cache first
"""

import argparse
import sys

spots = [
    {
        "name": "Willoway Park, Grand River",
        "lat": 42.9189496,
        "lng": -79.7674874,
        "question": "Why does Willoway Park on the Grand River hold channel catfish during midday heat in summer? What makes this spot worth fishing?",
    },
    {
        "name": "Byng Island, Grand River",
        "lat": 42.897598,
        "lng": -79.6398771,
        "question": "What makes Byng Island on the Grand River a good catfish spot? What species are present and when do they bite?",
    },
    {
        "name": "North London Athletic Fields, Thames River",
        "lat": 42.7853701,
        "lng": -81.471498,
        "question": "What species can be caught at North London Athletic Fields on the Thames River? What makes this stretch of the Thames productive for redhorse?",
    },
    {
        "name": "Credit River at Creditview Rd, Mississauga",
        "lat": 43.5716,
        "lng": -79.6883,
        "question": "What makes the Credit River near Creditview Road good for microfishing? What species are present and what habitat features concentrate them?",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Pre-warm FishBot synthesis cache")
    parser.add_argument(
        "--force", action="store_true",
        help="Clear existing cache before warming",
    )
    args = parser.parse_args()

    if args.force:
        from src.storage.database import get_db
        db = get_db()
        print("--force: clearing synthesis cache...")
        db.execute("DELETE FROM segment_synthesis")
        db.conn.commit()
        db.conn.close()
        print("Cache cleared.\n")

    from src.agent.chat import run_chat_api

    n_hits = 0
    n_miss = 0

    for i, spot in enumerate(spots, 1):
        print(f"[{i}/{len(spots)}] {spot['name']}")
        print(f"  Q: {spot['question'][:80]}...")

        messages = [{"role": "user", "content": spot["question"]}]
        result = run_chat_api(messages, user_id=1)  # the captain's prewarm

        mode = result.get("mode", "unknown")
        if mode == "synthesis_cache_hit":
            n_hits += 1
            print(f"  → CACHE HIT")
        else:
            n_miss += 1
            print(f"  → CACHE MISS (ran full pipeline, stored result)")

        reply_preview = result.get("reply", "")[:120].replace("\n", " ")
        print(f"  Reply: {reply_preview}...")
        print()

    total = len(spots)
    print(f"Done. {n_miss}/{total} computed and cached, {n_hits}/{total} already in cache.")


if __name__ == "__main__":
    main()
