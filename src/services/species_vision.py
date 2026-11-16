"""Claude vision species suggestion for a logged catch photo.

Mirrors vision_screening.py's image-analysis pattern. This is a suggestion
for the angler to confirm or correct, never a final answer — the candidate
list sent to the model is always the real regional species pool (see
query_deduped_species_ranges_for_jurisdiction), so the model structurally
cannot invent a species outside what actually lives here. Anything it
returns outside that list, or that it isn't at least low-confidence about,
comes back unresolved rather than guessed.
"""

import base64
import json
import logging

from sqlite_utils.db import Database

from src import llm
from src.storage.species_ranges import query_deduped_species_ranges_for_jurisdiction

logger = logging.getLogger(__name__)

_MAX_CANDIDATES = 3


def get_region_candidate_species(db: Database, jurisdiction: str = "CA-ON") -> list[str]:
    """Real, deduped common names for the region — the only species the
    vision model is allowed to suggest."""
    return [sr.species for sr in query_deduped_species_ranges_for_jurisdiction(db, jurisdiction)]


def suggest_species_from_photo(
    photo_bytes: bytes,
    candidate_species: list[str],
    media_type: str = "image/jpeg",
    *,
    user_id: int | None,
) -> dict:
    """Ask Claude to identify the fish in a catch photo, constrained to
    candidate_species only.

    Returns:
      {
        "screened": bool,           # False only on a hard failure (no API response)
        "candidates": [{"species": str, "confidence": "high"/"medium"/"low"}, ...],
        "unresolved": bool,         # True if nothing resolvable — never force a guess
        "note": str | None,        # brief reasoning from the model
      }
    """
    if not candidate_species:
        return {
            "screened": False,
            "candidates": [],
            "unresolved": True,
            "note": "no candidate species available",
        }

    image_data = base64.standard_b64encode(photo_bytes).decode()
    species_list = "\n".join(f"- {s}" for s in candidate_species)

    prompt = f"""You are looking at an angler's catch photo to suggest a species ID. \
This is a suggestion the angler will confirm or correct — never treat it as final.

The fish MUST be identified as one of these real regional species, or as unresolved:
{species_list}

Respond with ONLY valid JSON, no other text, in this exact shape:
{{
  "candidates": [
    {{"species": "<exact name from the list above>", "confidence": "high", "medium", or "low"}}
  ],
  "unresolved": true or false,
  "note": "brief reasoning — distinguishing features seen, or why it's uncertain"
}}

List up to {_MAX_CANDIDATES} candidates, ranked best first. Never invent a species name \
outside the list above. If the photo doesn't clearly show a fish, or you can't narrow it \
to any candidate with at least low confidence, return "candidates": [] and "unresolved": true."""

    try:
        response = llm.complete(
            "species_photo",
            caller="species_vision.suggest_species_from_photo",
            user_id=user_id,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        parsed = json.loads(raw)

        candidate_set = {s.lower() for s in candidate_species}
        candidates = [
            {"species": c["species"], "confidence": c.get("confidence", "low")}
            for c in (parsed.get("candidates") or [])
            if isinstance(c, dict) and (c.get("species") or "").lower() in candidate_set
        ][:_MAX_CANDIDATES]

        return {
            "screened": True,
            "candidates": candidates,
            "unresolved": bool(parsed.get("unresolved")) or not candidates,
            "note": parsed.get("note"),
        }
    except Exception as e:
        logger.warning("Species vision suggestion failed: %s", e)
        return {"screened": False, "candidates": [], "unresolved": True, "note": str(e)}


def annotate_conservation(db: Database, suggestion: dict | None) -> dict | None:
    """Attach the conservation flag to each suggested species.

    The candidate list is every species in the region, which correctly
    includes the listed ones — Redside Dace, Pugnose Shiner, Spotted Gar and
    American Eel are all in it. A photo of a Redside Dace *is* a Redside Dace,
    and dropping the name from the list would only make the identification
    wrong. But until now nothing downstream said so, so a listed species could
    be suggested, confirmed, and recorded with no conservation flag anywhere
    in the flow.

    Deliberately applied to the result and never to the prompt. Telling the
    vision model which candidates are protected would bias what it sees, and
    an identification bent by conservation status is worse than an
    unflagged one — it is wrong.

    The gate itself is untouched: this adds a field, and the angler still
    confirms or corrects exactly as before.
    """
    if not suggestion or not suggestion.get("candidates"):
        return suggestion

    from src.services.context import describe_species

    flagged = False
    for candidate in suggestion["candidates"]:
        try:
            ctx = describe_species(db, candidate["species"])
        except Exception:  # noqa: BLE001 - a lookup failure must not lose the ID
            logger.warning(
                "Conservation lookup failed for %s",
                candidate.get("species"),
                exc_info=True,
            )
            continue
        candidate["conservation_flag"] = ctx.sar_alert
        candidate["conservation_status_known_listed"] = ctx.status_known_listed
        candidate["conservation_note"] = ctx.sar_reason
        flagged = flagged or ctx.status_known_listed

    suggestion["any_candidate_listed"] = flagged
    if flagged:
        # Handling guidance, not a refusal. The fish is already in hand; what
        # is left to influence is how it goes back.
        suggestion["handling_note"] = (
            "At least one suggested species carries a conservation listing. "
            "Keep it wet, handle it as little as possible, and return it where "
            "it was caught. Confirm the identification before recording it."
        )
    return suggestion
