"""Save and load chat sessions for conversation persistence."""

from datetime import datetime
from uuid import uuid4

from sqlite_utils import Database

from src import llm


def get_session(db: Database, session_id: str, *, user_id: int) -> dict | None:
    """Return a session only to its owner."""
    return next(
        db.query(
            "SELECT * FROM chat_sessions WHERE session_id = ? AND user_id = ?",
            [session_id, user_id],
        ),
        None,
    )


def start_session(db: Database, session_id: str | None = None, *, user_id: int) -> str:
    """Resume an owned session or issue a new opaque ID.

    Unknown IDs, including another user's IDs, never become caller-chosen IDs.
    """
    if isinstance(session_id, str) and get_session(db, session_id, user_id=user_id):
        return session_id
    session_id = uuid4().hex
    db["chat_sessions"].insert(
        {
            "session_id": session_id,
            "user_id": user_id,
            "started_at": datetime.now().isoformat(),
            "turn_count": 0,
        }
    )
    return session_id


def save_turn(
    db: Database, session_id: str, role: str, content: str, turn_index: int, *, user_id: int
) -> None:
    """Save a single user or assistant message to an owned session."""
    if not get_session(db, session_id, user_id=user_id):
        raise ValueError("Chat session not found")
    if not isinstance(content, str) or not content.strip():
        return  # skip tool call blocks and empty messages
    db["chat_messages"].insert(
        {
            "session_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "turn_index": turn_index,
            "created_at": datetime.now().isoformat(),
        }
    )
    db.execute(
        "UPDATE chat_sessions SET turn_count = ? WHERE session_id = ? AND user_id = ?",
        [turn_index + 1, session_id, user_id],
    )
    db.conn.commit()


def end_session(db: Database, session_id: str, summary: str | None = None, *, user_id: int) -> None:
    """Mark an owned session as ended, optionally storing its summary."""
    db.execute(
        "UPDATE chat_sessions SET ended_at = ?, summary = ? WHERE session_id = ? AND user_id = ?",
        [datetime.now().isoformat(), summary, session_id, user_id],
    )
    db.conn.commit()


def generate_session_summary(
    messages: list[dict], *, user_id: int, session_id: str | None = None
) -> str:
    """Generate a compact summary of the session using Haiku."""
    text_messages = [
        m for m in messages if isinstance(m.get("content"), str) and m.get("content", "").strip()
    ]

    if not text_messages:
        return ""

    prompt = (
        "Summarize this fishing chat session in bullet points. "
        "Include: locations discussed, species targeted, tactics/baits mentioned, "
        "any plans made (trips, spots to try), and any conclusions reached. "
        "Be concise — this will be loaded as context in a future session.\n\n"
        + "\n".join(f"{m['role'].upper()}: {m['content'][:400]}" for m in text_messages[-20:])
    )

    response = llm.complete(
        "session_summary",
        caller="conversations.generate_session_summary",
        user_id=user_id,
        session_id=session_id,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def close_and_update_context(
    db: Database,
    session_id: str,
    messages: list[dict],
    *,
    user_id: int,
) -> None:
    """End a session: generate summary, merge into rolling angler context, mark ended.

    Skips the summary/merge step if the session had no meaningful turns.
    """
    from src.storage.angler_context import update_context

    row = get_session(db, session_id, user_id=user_id)
    if not row:
        return
    if (row.get("turn_count") or 0) < 1:
        end_session(db, session_id, user_id=user_id)
        return

    summary = generate_session_summary(messages, user_id=user_id, session_id=session_id)

    if summary and len(summary.strip()) > 100:
        update_context(db, summary, user_id=user_id)

    end_session(db, session_id, summary, user_id=user_id)
