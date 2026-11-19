"""
Test the YouTube ingest pipeline with a small batch of queries.
Run with: uv run python scripts/historical/test_youtube_ingest.py
"""

import sys

sys.path.insert(0, "src")

from dotenv import load_dotenv

load_dotenv()

from storage.database import get_db

from src.ingest.community.youtube_ingest import ingest_query, search_knowledge

db = get_db()

test_queries = [
    "channel catfish Grand River Dunnville",
    "redhorse sucker Thames River London Ontario",
    "carp fishing packbait corn",
]

for query in test_queries:
    print(f"\n{'=' * 60}")
    result = ingest_query(query, db, max_videos=5)
    print(f"Result: {result}")

# Test FTS search
print(f"\n{'=' * 60}")
print("Testing search: 'best bait for channel catfish'")
results = search_knowledge("best bait channel catfish", db, top_k=3)
if results:
    for r in results:
        print(f"\n[rank {r['rank']:.4f}] {r['title']}")
        print(f"  {r['url']}")
        print(f"  ...{r['chunk_text'][:200]}...")
else:
    print("(no results — FTS index may need content first)")

print("\nDone.")
