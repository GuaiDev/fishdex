"""
Persistent rolling context document for the angler.
A single document that accumulates knowledge across all chat sessions.
Updated after each meaningful session ends.
"""

from datetime import datetime

from sqlite_utils import Database

from src import llm

CONTEXT_TEMPLATE = """## Active plans
(none yet)

## Spots on the radar
(none yet)

## Learned patterns
(none yet)

## Species intel
(none yet)
"""

_MERGE_PROMPT = """\
You are maintaining a persistent fishing knowledge document for an angler.
Update the document below by merging in the new session summary.

RULES:
1. Keep all four sections: "Active plans", "Spots on the radar", "Learned patterns", "Species intel"
2. Active plans: add new trips being planned. When a trip has been LOGGED
   (appears in the user's trip history) or the date has clearly passed,
   move it from Active plans to a one-line "Completed trips" note, then
   remove it after 2 weeks. A plan that already happened should never stay
   in Active plans. If unsure whether a plan was completed, check if a
   matching session exists in Recent trips context.
3. Spots on the radar: add any new locations mentioned with coordinates if available.
   Include brief notes on what makes each spot notable. Keep indefinitely.
4. Learned patterns: add any confirmed fishing insights (what worked, what didn't,
   timing, conditions). These are permanent — never remove them.
5. Species intel: add species-specific tactics, bait, timing, or behaviour confirmed
   in conversation. Permanent.
6. If a section has nothing yet, write "(none yet)"
7. Be concise — each entry should be 1-2 lines max
8. LOCATION NAMES: You may include river or water body names when they are
   explicitly stated in the session summary. "Byng Island, Grand River" is
   correct if "Grand River" was mentioned. Do NOT invent or infer river names
   from your general knowledge — if the river wasn't stated in the summary,
   write just the location name without a river qualifier. A wrong river name
   is worse than no river name.
9. COORDINATES: If the session summary includes coordinates for a spot, include
   them in the spot entry in parentheses e.g. "Willoway Park, Grand River
   (42.917, -79.774)". Coordinates are unambiguous and more useful than names
   alone for large rivers with many distinct sections.
10. Do not add timestamps or dates unless they are part of an active plan
11. Output ONLY the updated document — no preamble, no explanation

EXISTING DOCUMENT:
{existing}

NEW SESSION SUMMARY:
{summary}

OUTPUT THE UPDATED DOCUMENT:"""


def load_context(db: Database, user_id: int = 1) -> str | None:
    """Load the current angler context document for a user. Returns None if none exists yet."""
    try:
        row = next(db["angler_context"].rows_where("user_id = ?", [user_id]), None)
        content = row["content"] if row else None
        return content if content and content.strip() else None
    except Exception:
        return None


def save_context(db: Database, content: str, user_id: int = 1) -> None:
    """Save the updated angler context document for a user, creating the row if needed."""
    existing = next(
        db["angler_context"].rows_where("user_id = ?", [user_id]),
        None,
    )
    if existing:
        db["angler_context"].update(
            existing["id"],
            {
                "content": content.strip(),
                "last_updated": datetime.now().isoformat(),
                "session_count": (existing.get("session_count") or 0) + 1,
            },
        )
    else:
        db["angler_context"].insert(
            {
                "user_id": user_id,
                "content": content.strip(),
                "last_updated": datetime.now().isoformat(),
            }
        )
    db.conn.commit()


def correct_context(db: Database, user_id: int = 1) -> None:
    """One-time correction for known wrong entries in the angler context document.

    Safe to run multiple times — only writes if a change is found.
    """
    current = load_context(db, user_id=user_id)
    if not current:
        return

    corrections = [
        ("Dunnville Dam tailwater, Welland River", "Dunnville Dam tailwater, Grand River"),
        ("Byng Island, Welland River", "Byng Island, Grand River"),
        ("Willoway Park, Welland River", "Willoway Park, Grand River"),
    ]

    updated = current
    changed = False
    for wrong, right in corrections:
        if wrong in updated:
            updated = updated.replace(wrong, right)
            changed = True

    if changed:
        save_context(db, updated, user_id=user_id)
        print("Angler context corrected.")


def _validate_context(context: str, session_summary: str, existing: str = "") -> list[str]:
    """Check for river names in context that weren't in the session summary or existing context."""
    import re

    warnings = []
    river_pattern = re.compile(r",\s*([\w\s]+River)", re.IGNORECASE)
    context_rivers = set(m.lower() for m in river_pattern.findall(context))
    summary_rivers = set(m.lower() for m in river_pattern.findall(session_summary))
    existing_rivers = set(m.lower() for m in river_pattern.findall(existing))
    hallucinated = context_rivers - summary_rivers - existing_rivers
    if hallucinated:
        warnings.append(
            f"River names in context not found in session summary: {hallucinated}. "
            "Verify these are correct."
        )
    return warnings


def update_context(db: Database, session_summary: str, user_id: int = 1) -> str:
    """Merge a new session summary into the existing angler context document.

    Returns the updated document text.
    """
    existing = load_context(db, user_id=user_id) or CONTEXT_TEMPLATE

    prompt = _MERGE_PROMPT.format(existing=existing, summary=session_summary)

    response = llm.complete(
        "context_merge",
        caller="angler_context.update_context",
        user_id=user_id,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )

    updated = response.content[0].text.strip()
    save_context(db, updated, user_id=user_id)
    warnings = _validate_context(updated, session_summary, existing)
    for w in warnings:
        print(f"[CONTEXT WARNING] {w}")
    return updated


def format_context_for_prompt(context: str) -> str:
    """Format the angler context document for injection into the system prompt."""
    return "## What I know about your fishing\n\n" + context + "\n\n---"
