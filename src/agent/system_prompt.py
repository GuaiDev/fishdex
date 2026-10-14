"""Assemble the system prompt from template + dynamic context.

The template at prompts/system.md holds the bot's persona and rules. The runtime
appends three sections per conversation: profile, recent trips, active jurisdiction.
"""

from datetime import datetime
from pathlib import Path

from src.jurisdictions.base import Jurisdiction
from src.models.profile import UserProfile
from src.models.trip import Trip

TEMPLATE_PATH = Path("prompts/system.md")


def load_template(path: Path | None = None) -> str:
    p = path or TEMPLATE_PATH
    return p.read_text()


def assemble(
    template: str,
    profile: UserProfile | None,
    recent_trips: list[Trip],
    active_jurisdiction: Jurisdiction,
) -> str:
    """Build the system prompt.

    `profile=None` omits the profile and recent-trips sections entirely. The
    profile file and the legacy trips table belong to one angler; a prompt for
    anyone else must not carry them, not even as an empty "No trips logged yet".
    """
    parts = [template.rstrip(), "", "---", ""]
    if profile is not None:
        parts += [_format_profile(profile), "", _format_recent_trips(recent_trips), ""]
    parts.append(_format_jurisdiction(active_jurisdiction))
    return "\n".join(parts)


def _format_profile(profile: UserProfile) -> str:
    home = profile.home_location.name if profile.home_location else "(not set)"
    species = ", ".join(profile.target_species) or "(not set)"
    lines = [
        "## Your angler",
        "",
        f"- Home: {home} ({profile.home_jurisdiction})",
        f"- Target species: {species}",
        f"- Fishing style: {profile.fishing_style or '(not set)'}",
    ]
    if profile.frequented_jurisdictions:
        lines.append(f"- Also fishes: {', '.join(profile.frequented_jurisdictions)}")
    if profile.preferences:
        lines.append(f"- Preferences: {profile.preferences}")
    if profile.gear:
        gear_summary = "; ".join(", ".join(f"{k}: {v}" for k, v in g.items()) for g in profile.gear)
        lines.append(f"- Gear: {gear_summary}")
    profile_note = ""
    if profile.updated_at:
        days_old = (datetime.now() - profile.updated_at).days
        if days_old > 90:
            profile_note = (
                f"\n⚠️ Profile last updated {days_old} days ago — "
                "angler context document is more reliable for current preferences."
            )
    else:
        profile_note = (
            "\n(Profile has no update timestamp — "
            "defer to angler context document for current preferences.)"
        )
    if profile_note:
        lines.append(profile_note)
    lines.append(
        "Note: The angler context document below reflects actual fishing activity "
        "and takes precedence over this profile for species preferences and patterns."
    )
    return "\n".join(lines)


def _format_recent_trips(trips: list[Trip]) -> str:
    if not trips:
        return "## Recent trips\n\nNo trips logged yet."
    lines = ["## Recent trips", ""]
    for t in trips:
        if t.species_caught:
            species_summary = ", ".join(
                c.species + (f" ({c.length_cm}cm)" if c.length_cm else "") for c in t.species_caught
            )
        else:
            species_summary = "skunked"
        notes_bits: list[str] = []
        if t.what_worked:
            notes_bits.append(f"worked: {t.what_worked}")
        if t.what_didnt:
            notes_bits.append(f"didn't: {t.what_didnt}")
        if t.notes:
            notes_bits.append(t.notes)
        suffix = f" — {'; '.join(notes_bits)}" if notes_bits else ""
        lines.append(
            f"- {t.date.isoformat()} at {t.location_name} ({t.jurisdiction}): "
            f"{species_summary}{suffix}"
        )
    return "\n".join(lines)


def _format_jurisdiction(j: Jurisdiction) -> str:
    return "## Active jurisdiction\n\n" + j.regulatory_context()
