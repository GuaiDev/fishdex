"""
One-time script to seed the angler context from existing session summaries.
Run with: uv run python scripts/seed_angler_context.py
"""
import sys
sys.path.insert(0, "src")

from storage.angler_context import update_context
from storage.database import ensure_schema, get_db

db = get_db()
ensure_schema(db)

summaries = list(db.execute("""
    SELECT summary, started_at FROM chat_sessions
    WHERE summary IS NOT NULL
      AND LENGTH(summary) > 100
    ORDER BY started_at ASC
""").fetchall())

if not summaries:
    print("No existing summaries to seed from.")
else:
    print(f"Seeding context from {len(summaries)} existing session(s)...")
    for summary, started_at in summaries:
        print(f"  Processing session from {started_at[:16]}...")
        update_context(db, summary, user_id=1)
    print("Done. Run 'fishbot context' to see the result.")
