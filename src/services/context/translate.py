"""Static raw-value → plain-language lookup.

A number without a "so what" is noise dressed as rigour. Every translator here
returns None when it cannot say something an angler could act on, and callers
drop the value entirely in that case.

This is a lookup, not an LLM call, and it lives in one place so that seven
callers do not each reinvent what "EPT 0.42" means.

It also holds the one thing here that is not about a measured value: the
"why fish gather here" paragraph for each kind of spot. That text is general
reasoning about a kind of water, so it is served as INFERENCE with a fixed
label and never rides inside a RECORD field about a particular place.
"""

from src.models.context import ContextField


def substrate(category: str | None) -> str | None:
    """Surficial geology → what it does to the water."""
    if not category:
        return None
    match category.strip().lower():
        case "bedrock":
            return "runs clearer and holds temperature better than soft-bottom reaches"
        case "coarse":
            return "gravel and cobble — spawning substrate, and it holds insect life"
        case "mixed":
            return "varied bottom, so holding water changes character reach to reach"
        case "fine":
            return "silt and sand — turbid after rain, fewer clean spawning beds"
        case "organic":
            return "mucky bottom typical of wetland reaches — warmer, lower oxygen"
    return None


def barriers(upstream: int | None, downstream: int | None) -> str | None:
    """Barrier counts → how fish actually move through the reach."""
    if upstream is None and downstream is None:
        return None
    up = upstream or 0
    down = downstream or 0
    if up == 0 and down == 0:
        return "no mapped barriers either way"
    if up > 0 and down == 0:
        return "a mapped barrier lies upstream of here and none lies downstream"
    if down > 0 and up == 0:
        return "a mapped barrier lies downstream and none upstream"
    return "mapped barriers lie both upstream and downstream"


def ept_proportion(value: float | None) -> str | None:
    """EPT share of the benthic community → year-round carrying capacity."""
    if value is None:
        return None
    if value >= 0.40:
        return "healthy insect life, holds fish year-round"
    if value >= 0.20:
        return "moderate insect life — supports fish but not densely"
    return "sparse mayfly/stonefly/caddis presence, a sign of degraded water"


def dissolved_oxygen(mgl: float | None) -> str | None:
    """DO → a hard constraint, never a presence indicator."""
    if mgl is None:
        return None
    if mgl < 4.0:
        return "below the floor for a persistent fish community"
    if mgl < 6.0:
        return "low — tolerant species only (bullhead, carp, sucker)"
    if mgl >= 9.0:
        return "well oxygenated, cold enough for trout if temperature agrees"
    return "adequate for most warmwater and coolwater species"


def thermal_regime(regime: str | None) -> str | None:
    if not regime or regime.strip().lower() == "unknown":
        return None
    match regime.strip().lower():
        case "coldwater":
            return "trout and salmon water"
        case "coolwater":
            return "pike, walleye and smallmouth water"
        case "warmwater":
            return "bass, panfish and catfish water"
    return None


def ph(value: float | None) -> str | None:
    """Only worth surfacing at the extremes."""
    if value is None:
        return None
    if value < 5.5:
        return "acidic enough to limit which species persist"
    if value > 9.0:
        return "unusually alkaline — stressful for most freshwater species"
    return None  # 5.5-9.0 is unremarkable; saying so wastes the reader's time


def flow_trend(trend: str | None) -> tuple[str, str] | None:
    """A gauge's trend as (word, plain statement), or None when it has none.

    The gauge reports "stable"; the panel says "steady", the word already used
    for pressure. There is deliberately no "normal" here: a normal needs a
    seasonal baseline for this gauge, and we do not hold one. The statement
    says only what was measured — a change over the last three hours.
    """
    if not trend:
        return None
    match trend.strip().lower():
        case "rising":
            return "rising", "rising over the last 3 hours"
        case "falling":
            return "falling", "falling over the last 3 hours"
        case "stable" | "steady":
            return "steady", "little change over the last 3 hours"
    return None


def pressure_trend(trend: str | None) -> str | None:
    if not trend:
        return None
    match trend.strip().lower():
        case "falling":
            return "falling pressure ahead of weather often triggers a feeding window"
        case "rising":
            return "rising pressure after a front usually means a slower bite"
        case "steady":
            return None  # steady pressure implies nothing actionable
    return None


def stream_order(order: int | None) -> str | None:
    if order is None:
        return None
    if order <= 2:
        return "a small headwater reach — in urban areas these are often culverted"
    if order <= 4:
        return "a mid-size stream, wadeable in most conditions"
    return "a substantial river reach"


def access_score(value: float | None) -> str | None:
    """What a normalised access score means for getting to the water.

    The number itself is a rank within Ontario, not a distance to a car park,
    so it only travels with a plain reading of what it implies on the ground.
    """
    if value is None:
        return None
    if value >= 0.7:
        return "roads and parking close by — easy to reach, and others reach it too"
    if value >= 0.4:
        return "reachable, but expect a walk in"
    return "little mapped road or parking nearby — plan the approach before going"


# Values we deliberately never surface: no honest "so what" exists for an
# angler, so showing the number would be rigour theatre.
def conductivity(_value: float | None) -> None:
    """Always None. Kept as a named function so the omission is explicit."""
    return None


def turbidity_fnu(_value: float | None) -> None:
    """Always None — the clarity proxy anglers use is visual, not FNU."""
    return None


# ── why fish gather here ──────────────────────────────────────────────────────

GENERAL_REASONING_LABEL = "General reasoning, not a record about this spot"
GENERAL_REASONING_SOURCE = "General reasoning about this kind of water"

# One entry per kind of spot, exactly two sentences each, no seasonal claims and
# no gear advice (the project removed its last unsourced tactics module). Words
# a beginner would not know are kept out; a test enforces it. Quiet water is a
# ranking of reaches, not a located structure, so its entry says what the
# ranking is NOT: a claim about fish.
WHY_FISH_GATHER: dict[str, str] = {
    "river_mouth": (
        "Fish that live in the lake pass through here to feed or to run upstream. "
        "The river also carries food out into open water."
    ),
    "creek_junction": (
        "Two flows mixing makes a seam between fast and slow water, which fish use "
        "for shelter with food drifting past. A creek that runs cooler than the river can "
        "also give fish a place to ease out of warm water."
    ),
    "below_dam": (
        "A dam or weir blocks fish from moving further upstream, so they gather in the "
        "water just below it. That water is churned up and rich in oxygen."
    ),
    "public_fishing": (
        "Someone has mapped this as a place to fish, usually because the water is easy "
        "and allowed to reach. That is about access, so it says nothing by itself about "
        "what is swimming there."
    ),
    "quiet_water": (
        "These reaches have few public observation records. That can mean fewer anglers, "
        "few observers or hard access, and it never tells you whether fish are there."
    ),
}


def why_fish_gather(kind: str) -> ContextField:
    """The kind's two-sentence explanation, as inferred, general-reasoning text.

    Returned as an `inferred` field so it can never render like a record: the
    provenance names it as reasoning about the kind of water, and the label
    says it is not a fact about this spot.
    """
    text = WHY_FISH_GATHER.get(kind)
    if text is None:
        return ContextField.inferred(None)
    return ContextField.inferred(text, meaning=GENERAL_REASONING_LABEL)


# Words a beginner may meet in the cards' explanations, and what they mean. The
# cards avoid them; the glossary is for the one place they cannot be avoided
# (a map label, a question to the bot) and is the only text allowed to use them.
GLOSSARY: dict[str, str] = {
    "weir": (
        "A weir is a low wall built across a river that water flows over. "
        "It holds the water back a little."
    ),
    "confluence": "A confluence is the place where two streams meet and become one.",
    "tailwater": "A tailwater is the stretch of river just downstream of a dam.",
}
