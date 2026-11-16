"""Save and load chat sessions for conversation persistence."""

from datetime import datetime

from sqlite_utils import Database

from src import llm


def start_session(db: Database, session_id: str) -> None:
    """Create a new session record (no-op if already exists)."""
    db["chat_sessions"].insert(
        {
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "turn_count": 0,
        },
        ignore=True,
    )


def save_turn(db: Database, session_id: str, role: str, content: str, turn_index: int) -> None:
    """Save a single user or assistant message."""
    if not isinstance(content, str) or not content.strip():
        return  # skip tool call blocks and empty messages
    db["chat_messages"].insert({
        "session_id": session_id,
        "role": role,
        "content": content,
        "turn_index": turn_index,
        "created_at": datetime.now().isoformat(),
    })
    db["chat_sessions"].update(session_id, {"turn_count": turn_index + 1})


def end_session(db: Database, session_id: str, summary: str | None = None) -> None:
    """Mark session as ended, optionally store a summary."""
    db["chat_sessions"].update(session_id, {
        "ended_at": datetime.now().isoformat(),
        "summary": summary,
    })


def generate_session_summary(
    messages: list[dict], user_id: int = 1, session_id: str | None = None
) -> str:
    """Generate a compact summary of the session using Haiku."""
    text_messages = [
        m for m in messages
        if isinstance(m.get("content"), str) and m.get("content", "").strip()
    ]

    if not text_messages:
        return ""

    prompt = (
        "Summarize this fishing chat session in bullet points. "
        "Include: locations discussed, species targeted, tactics/baits mentioned, "
        "any plans made (trips, spots to try), and any conclusions reached. "
        "Be concise — this will be loaded as context in a future session.\n\n"
        + "\n".join(
            f"{m['role'].upper()}: {m['content'][:400]}"
            for m in text_messages[-20:]
        )
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
    user_id: int = 1,
) -> None:
    """End a session: generate summary, merge into rolling angler context, mark ended.

    Skips the summary/merge step if the session had no meaningful turns.
    """
    from src.storage.angler_context import update_context

    try:
        row = db["chat_sessions"].get(session_id)
    except Exception:
        return

    if not row or (row.get("turn_count") or 0) < 1:
        end_session(db, session_id)
        return

    summary = generate_session_summary(messages, user_id=user_id, session_id=session_id)

    if summary and len(summary.strip()) > 100:
        update_context(db, summary, user_id=user_id)

    end_session(db, session_id, summary)
