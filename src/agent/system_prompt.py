"""Assemble the system prompt from template + dynamic context.

The template at prompts/system.md holds the bot's persona and rules. The runtime
appends two sections per conversation, both read for that user from the database:
the angler (home, jurisdiction, blended profile) and the active jurisdiction.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.jurisdictions.base import Jurisdiction
from src.jurisdictions.registry import get_jurisdiction
from src.models.profile import Location
from src.services.context import angler_profile
from src.services.context.render import render_angler_profile
from src.settings import default_jurisdiction
from src.storage.profile import get_user_home

TEMPLATE_PATH = Path("prompts/system.md")


def load_template(path: Path | None = None) -> str:
    p = path or TEMPLATE_PATH
    return p.read_text()


@dataclass(frozen=True)
class PromptInputs:
    """What one user's prompt is built from, all read from the database."""

    home_location: Location | None
    jurisdiction: Jurisdiction
    profile: Any
    """The blended angler profile (`context.angler_profile`)."""


def prompt_inputs(db: Any, user_id: int) -> PromptInputs:
    """Home, jurisdiction and blended angler profile for one user.

    Nothing here is shared between users: no profile file, no legacy `trips`
    table. A user with no stored jurisdiction gets the deploy default, never
    another user's home. A jurisdiction does not require coordinates.
    """
    stored = get_user_home(db, user_id)
    home, code = stored if stored is not None else (None, None)
    return PromptInputs(
        home_location=home,
        jurisdiction=get_jurisdiction(code or default_jurisdiction()),
        profile=angler_profile(db, user_id=user_id),
    )


def assemble(template: str, db: Any, user_id: int) -> str:
    """Build the system prompt for one user."""
    inputs = prompt_inputs(db, user_id)
    parts = [
        template.rstrip(),
        "",
        "---",
        "",
        _format_angler(inputs),
        "",
        _format_jurisdiction(inputs.jurisdiction),
    ]
    return "\n".join(parts)


def _format_angler(inputs: PromptInputs) -> str:
    home = inputs.home_location
    lines = [
        "## Your angler",
        "",
        f"- Home: {home.name or 'a saved point'}" if home else "- Home: (not set)",
        f"- Home jurisdiction: {inputs.jurisdiction.code}",
    ]
    lines += render_angler_profile(inputs.profile)
    return "\n".join(lines)


def _format_jurisdiction(j: Jurisdiction) -> str:
    return "## Active jurisdiction\n\n" + j.regulatory_context()
