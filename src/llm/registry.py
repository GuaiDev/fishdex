"""Role -> model registry.

Call sites name a *role* ("router", "coaching"), never a model id, so changing
which model does a job is one edit here instead of a hunt through ten files.

Two override levels, both environment variables so a deploy can change a model
without a code change:

    FISHDEX_MODEL_<ROLE>   one role, e.g. FISHDEX_MODEL_ROUTER
    CLAUDE_MODEL           every role marked `follows_main_model` (the jobs that
                           used to read `get_model()`)
"""

import os
from dataclasses import dataclass

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class Role:
    model: str
    follows_main_model: bool = False


ROLES: dict[str, Role] = {
    # The agent itself and the jobs that always followed CLAUDE_MODEL.
    "chat": Role(SONNET, follows_main_model=True),
    "trip_parse": Role(SONNET, follows_main_model=True),
    "species_photo": Role(SONNET, follows_main_model=True),
    # Pinned to Sonnet before the gateway existed, and not CLAUDE_MODEL-driven.
    "vision_screen": Role(SONNET),
    # Cheap, short jobs.
    "router": Role(HAIKU),
    "location_extract": Role(HAIKU),
    "reflex": Role(HAIKU),
    "synthesis_answer": Role(HAIKU),
    "history_summary": Role(HAIKU),
    "session_summary": Role(HAIKU),
    "context_merge": Role(HAIKU),
    "insight_revision": Role(HAIKU),
    "coaching": Role(HAIKU),
    "web_escalation": Role(HAIKU),
}


def model_for(role: str) -> str:
    """The model id for a role. An unknown role is a bug, so it raises."""
    try:
        entry = ROLES[role]
    except KeyError:
        raise KeyError(f"unknown LLM role {role!r}; add it to src/llm/registry.py") from None

    override = os.environ.get(f"FISHDEX_MODEL_{role.upper()}")
    if override:
        return override
    if entry.follows_main_model:
        from src.llm.client import load_env

        load_env()
        return os.environ.get("CLAUDE_MODEL") or entry.model
    return entry.model
