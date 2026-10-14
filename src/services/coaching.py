"""On-demand coaching, built on the central context layer.

Previously this service assembled its own prompt block from raw `stops` rows:
its own formatting, its own idea of what an empty log meant, its own decision
about which columns mattered. Two consequences followed from that, and both
were bugs rather than style:

  * Stored insights reached the model as anonymous sentences. An insight the
    assistant synthesised and one drawn from a survey rendered identically.
  * Nothing consulted the conservation layer. A coaching question about a
    listed species produced targeting advice, because the SAR check lived in a
    different code path that this one never called.

Both are properties of bypassing the context layer, so the fix is to stop
bypassing it. Retrieval and rendering happen there; what remains here is the
coaching question itself and the rules the answer has to obey.
"""

import logging

from sqlite_utils import Database

from src.agent.client import get_client
from src.services.context import describe, describe_species, species_history, user_layer
from src.services.context.render import (
    render_place_context,
    render_species_context,
    render_species_history,
    render_user_layer,
)

logger = logging.getLogger(__name__)

HAIKU = "claude-haiku-4-5-20251001"

# The epistemic rule, stated once and shared by both coaching paths. General
# ecological knowledge applied to observed conditions is legitimate at n=1;
# claims about this angler's own tendencies need both arms of a comparison.
_RULES = """Rules you must follow:

- Every fact below carries its source in square brackets. A claim marked
  "reasoning, no source" or "web, unverified" is NOT a record — do not present
  it as one, and say which kind of thing you are relying on.
- General fishing and ecology principles applied to the conditions shown are
  fine to state directly, even from a single trip.
- Claims about THIS angler's own patterns ("you do better in X") require a
  pattern marked claimable below. If it says NOT yet claimable, you may raise
  it as a hypothesis to test, never as a finding.
- Do not invent sessions, patterns or conditions that are not shown.
- Where the data is empty, the reason is given. Say which gap it is; "no data"
  on its own is not an acceptable answer.
- Blanks are not failures. They are half the signal and worth analysing.
- Match the register per concept, from the profile lines: say the map words
  they know plainly, define the ones they have met in a few words, and explain
  any other in plain words first. There is no overall skill level; never
  describe the angler as a beginner or an expert."""

_SAR_RULES = """
CONSERVATION OVERRIDE — this species carries a conservation flag.
Do not explain how to find, target, catch or handle-for-photos this species.
Species at Risk law prohibits capture, not merely possession, so
catch-and-release is not an exemption. Answer the question only insofar as it
can be answered without targeting guidance, say plainly why you are declining
the rest, and suggest the angler verify the current listing themselves."""


def _ask(prompt: str, max_tokens: int) -> str:
    client = get_client()
    resp = client.messages.create(
        model=HAIKU,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def get_species_coaching(
    db: Database,
    species: str,
    specific_question: str | None = None,
    user_id: int = 1,
) -> str:
    """Coach on one species, across everywhere the angler has fished it."""
    history = species_history(db, species, user_id=user_id)
    layer = user_layer(db, user_id=user_id)
    species_ctx = describe_species(db, species)

    question = (
        specific_question or f"What should I do differently to catch more {species}?"
    )

    blocks = [
        "You are a fishing coach working only from this angler's logged data "
        "and the records below.",
        f'The angler asked: "{question}"',
        render_species_context(species_ctx),
        render_species_history(history),
        render_user_layer(layer),
        _RULES,
    ]
    # Gated on an affirmative listing signal, not on `sar_alert`. Every species
    # in the local file is unverified, so gating on the alert would refuse
    # coaching for every fish in Ontario — a rule that fires on everything
    # protects nothing and just trains the user to ignore it. The unverified
    # caution still reaches the model through the species block above.
    if species_ctx.status_known_listed:
        blocks.append(_SAR_RULES)
    else:
        blocks.append(
            "Structure your answer as: what the data shows, what is missing "
            "from the log, what to try differently, and one concrete "
            "experiment for the next trip. 150-250 words."
        )

    return _ask("\n\n".join(blocks), max_tokens=500)


def get_location_coaching(
    db: Database,
    location_query: str,
    specific_question: str | None = None,
    user_id: int = 1,
) -> str:
    """Coach on one place — the angler's history there plus what is recorded.

    The old version refused outright when the angler had never logged a trip
    at the location. That threw away everything the corpus knows about the
    water, which is the more useful half of the answer for somewhere they have
    not been yet.
    """
    ctx = describe(db, query=location_query, caller="coach", user_id=user_id)
    if ctx is None:
        return (
            f"I couldn't resolve '{location_query}' to a specific stretch of water. "
            "Try a named creek or river, or give me coordinates."
        )

    layer = user_layer(db, user_id=user_id)
    question = (
        specific_question or f"What should I do differently at {location_query}?"
    )

    prompt = "\n\n".join(
        [
            "You are a fishing coach working only from the records below.",
            f'The angler asked: "{question}"',
            render_place_context(ctx),
            render_user_layer(layer),
            _RULES,
            "Structure your answer as: what worked here, what didn't, and one "
            "specific thing to try next visit. If this angler has never fished "
            "here, say so and work from what is recorded about the water "
            "instead. 150-200 words.",
        ]
    )
    return _ask(prompt, max_tokens=450)
