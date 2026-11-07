"""The style brief the coach is given about one angler.

Wording lives here, next to `words.py`, so it is worded once. Nothing in it names
a province, state, regulation or species: styles and water types are the same
everywhere. A style shapes emphasis only. It never decides which facts are shown,
and it never overrides what the angler actually asked.
"""

HEADER = "### Angler style (shapes emphasis only, never which facts are shown)"

UNKNOWN = (
    "- Style is not known yet. Do not assume one, and do not ask the angler to describe "
    "their style. Offer the same neutral kind of next step to everyone."
)

EXPLORER = (
    "- From the log, this angler tends to try new water. For an open-ended question, "
    "lean on what there is to find: how to read unfamiliar water on arrival, what is "
    "recorded nearby, what to note so the next new place can be compared. A good next "
    "step is a scouting one: a different stretch or kind of water, with two or three "
    "things to observe. A repeat visit is never a failure."
)

REGULAR = (
    "- From the log, this angler tends to go back to the same water. For an open-ended "
    "question, lean on what there is to learn by returning: what changed between visits "
    "(flow, temperature, season, time of day), and what the log can and cannot show yet. "
    "A good next step is a controlled one: same place, change one thing. Do not push "
    "somewhere new, and never treat a blank as a reason to leave."
)

MIXED = (
    "- From the log, this angler mixes new water with places they know. Offer exactly one "
    "next step, picked by the question: a scouting step (new water) if it points outward, "
    "a controlled step (same place, change one thing) if it points to a known place. "
    "Never offer both."
)

WATER_TYPE = (
    "- They mostly go to {water}. Use examples from that kind of water. If the question is "
    "about another kind of water, say what is different about it; do not discourage it, and "
    "do not suggest any kind of water holds more fish."
)

DECLARED_NOTE = "(the angler set this themselves; it outranks what the log suggests)"

DIRECT_QUESTION = (
    "- A question that clearly asks for a specific direction is answered directly, with "
    "this style left out. Examples: asking about a particular place, whatever their usual "
    "habit, or asking for somewhere new, whatever their usual habit. Style only shapes "
    "open-ended questions."
)

QUIET = (
    "- Do not tell the angler which style you assumed, and do not ask them to confirm it, "
    "unless they ask. Style never changes the facts shown or any conservation rule."
)
