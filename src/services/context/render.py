"""One renderer, for every surface.

The context layer's guarantee is that provenance is a data field rather than a
paragraph of prompt prose. That guarantee survives only as far as the last
place the data is turned into text — so it is turned into text exactly once,
here, and every caller uses this module.

The alternative is each call site formatting its own block, which reproduces
the drift this layer exists to remove: seven copies of the rendering rules,
each free to quietly drop a source or flatten an empty reason into "no data".

Rendering rules, applied uniformly:

  * A value never appears without its provenance.
  * An empty field never renders as absence alone — it renders as *why*.
  * A number never appears without its "so what"; if the translation layer
    has no meaning for it, the number is withheld and the fact that we hold a
    reading is not.
  * Record dates are surfaced, never a confidence score. A 2001 museum
    specimen and a 2022 sighting are not peers and must not read as peers.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from src.models.angler_profile import AnglerProfile
from src.models.context import (
    AccessSlice,
    ConditionsSlice,
    ContextField,
    ExploreResponse,
    HistorySlice,
    Place,
    PlaceContext,
    RecordedInsight,
    RecordsSlice,
    SpecialHere,
    SpeciesContext,
    SpeciesHistory,
    StructureSlice,
    UserLayer,
    WaterSlice,
)
from src.models.spot import AccessRef, Spot
from src.models.spot_card import (
    AccessItem,
    CardCaveat,
    CardFact,
    CardHook,
    CardMeta,
    CardSpecies,
    CrownLandFlag,
    GeneralReasoning,
    GettingThereBlock,
    GlossaryTerm,
    MightFindBlock,
    NewToYouBlock,
    SpecialHereBlock,
    SpecialHereSpeciesLine,
    SpotCard,
    WhyGatherBlock,
)
from src.services import spot_labels, terms
from src.services.context import translate
from src.services.new_to_you import MIN_SPECIES_FOR_HOOK, is_caught

# A record older than this is called out in the rendering. Not a cutoff and not
# a confidence penalty — the angler weighs it. We only make sure the date is
# impossible to miss, because for a species that may no longer be present the
# date is the only honest signal we have.
_STALE_YEARS = 10


def _line(label: str, field: ContextField) -> str | None:
    """One `label: value [source]` line, or the reason there isn't one.

    Returns None only for fields that were never touched at all — a default
    ContextField with neither value nor reason. Those are slices the bundle
    didn't populate, and printing "unknown" for them would misreport a
    deliberate omission as a gap in the data.
    """
    if field.is_empty and field.empty_reason is None:
        return None
    return f"- {label}: {field.explain()}"


def _block(title: str, lines: list[str | None]) -> str:
    body = [ln for ln in lines if ln]
    if not body:
        return ""
    return "\n".join([f"### {title}", *body])


# -- place ---------------------------------------------------------------------


def render_place(place: Place) -> str:
    bits = [f"**{place.name or place.query}** ({place.lat:.4f}, {place.lng:.4f})"]
    bits.append(f"searched within {place.radius_km:g} km")
    if place.jurisdiction:
        bits.append(place.jurisdiction)
    head = " — ".join(bits)
    if place.resolution_note:
        head += f"\n_{place.resolution_note}_"
    return head


# -- records -------------------------------------------------------------------


def render_records(records: RecordsSlice, today_year: int | None = None) -> str:
    if not records.species:
        reason = records.empty_reason.value if records.empty_reason else "unknown"
        head = [
            "### Species recorded",
            f"Nothing within {records.radius_km:g} km ({reason}). This is a "
            f"statement about the corpus, not about the water.",
        ]
        # The bird proxy still matters here — arguably more than anywhere else.
        # No fish records plus active ospreys is a different situation from no
        # fish records and nothing else either.
        proxy = _line("fish-eating birds", records.piscivore_activity)
        if proxy:
            head.append(proxy)
        return "\n".join(head)

    lines = [f"### Species recorded ({records.total_count} records)"]
    for rec in records.species:
        parts = [rec.species]
        if rec.common_name and rec.common_name.lower() != rec.species.lower():
            parts.append(f"({rec.common_name})")
        detail = " ".join(parts)
        suffix = [f"{rec.count}x"]
        if rec.most_recent:
            suffix.append(f"most recent {rec.most_recent}")
            if _is_stale(rec.most_recent, today_year):
                suffix.append("old record, the fish may no longer be there")
        if rec.is_obscured:
            suffix.append("location fuzzed to ~22 km by the observer's geoprivacy")
        lines.append(f"- {detail}: {', '.join(suffix)} [{rec.provenance.describe()}]")

    if records.escalated_to_web:
        lines.append("- _Some of the above came from a live web search and is unverified._")
    proxy = _line("fish-eating birds", records.piscivore_activity)
    if proxy:
        lines.append(proxy)
    return "\n".join(lines)


def _is_stale(date_text: str, today_year: int | None) -> bool:
    if today_year is None:
        from datetime import UTC, datetime

        today_year = datetime.now(UTC).year
    year = date_text[:4]
    if not year.isdigit():
        return False
    return today_year - int(year) >= _STALE_YEARS


# -- the physical slices -------------------------------------------------------


def render_water(water: WaterSlice) -> str:
    return _block(
        "Water",
        [
            _line("thermal regime", water.thermal_class),
            _line("substrate", water.substrate),
            _line("dissolved oxygen", water.dissolved_oxygen),
            _line("pH", water.ph),
            _line("insect life", water.benthic_health),
        ],
    )


def render_structure(structure: StructureSlice) -> str:
    return _block(
        "Structure",
        [
            _line("barriers upstream", structure.barriers_upstream),
            _line("barriers downstream", structure.barriers_downstream),
            _line("confluence", structure.is_confluence),
            _line("connects to", structure.waterbody_connection),
            _line("stream order", structure.stream_order),
        ],
    )


def render_access(access: AccessSlice) -> str:
    return _block(
        "Access",
        [
            _line("parking", access.parking),
            _line("trails", access.trails),
            _line("crown land", access.crown_land),
            _line("note", access.access_note),
        ],
    )


def render_conditions(conditions: ConditionsSlice) -> str:
    return _block(
        "Conditions now",
        [
            _line("air temp", conditions.air_temp_c),
            _line("pressure", conditions.pressure_trend),
            _line("river flow", conditions.flow_cms),
            _line("flow trend", conditions.flow_trend),
            _line("water level", conditions.level_m),
            _line("water temp", conditions.water_temp_c),
        ],
    )


def render_history(history: HistorySlice) -> str:
    """The user's own record here — including the blanks.

    Blanks are printed as a first-class number rather than implied by
    subtraction. They are half the analytical signal and the product does not
    treat them as a failure state to be tidied away.
    """
    if history.visits == 0:
        reason = history.empty_reason.value if history.empty_reason else "no visits logged"
        return f"### Your history here\nNone ({reason})."

    lines = [
        "### Your history here",
        f"- {history.visits} visit(s): {history.productive_visits} productive, "
        f"{history.blanks} blank",
    ]
    if history.last_visit:
        lines.append(f"- last visit: {history.last_visit}")
    if history.species_caught:
        lines.append(f"- caught here: {', '.join(history.species_caught)}")
    if history.techniques_used:
        lines.append(f"- techniques tried: {', '.join(history.techniques_used)}")
    return "\n".join(lines)


# -- whole contexts ------------------------------------------------------------


def render_place_context(ctx: PlaceContext) -> str:
    """The full block for one place, in the order an angler reads it."""
    sections = [render_place(ctx.place)]
    if ctx.records is not None:
        sections.append(render_records(ctx.records))
    if ctx.water is not None:
        sections.append(render_water(ctx.water))
    if ctx.structure is not None:
        sections.append(render_structure(ctx.structure))
    if ctx.access is not None:
        sections.append(render_access(ctx.access))
    if ctx.conditions is not None:
        sections.append(render_conditions(ctx.conditions))
    if ctx.history is not None:
        sections.append(render_history(ctx.history))
    return "\n\n".join(s for s in sections if s)


def render_species_context(ctx: SpeciesContext) -> str:
    if not ctx.found:
        return f"### {ctx.species}\nNot in the local species file. {ctx.sar_reason}"

    head = ctx.species
    if ctx.scientific_name and ctx.scientific_name.lower() != ctx.species.lower():
        head += f" (*{ctx.scientific_name}*)"

    lines = [f"### {head}"]
    if ctx.sar_alert:
        # Rendered before anything else and never as a footnote: SAR law
        # prohibits capture, not just possession, so catch-and-release is not
        # an exemption and the reader has to see this before the habitat note.
        lines.append(f"- **Conservation flag: {ctx.sar_reason}**")
        if ctx.status_known_listed:
            lines.append("- Do not suggest how to target this species. Handling guidance only.")
        else:
            # No affirmative listing signal, only an unverified status. The
            # corpus's own angling text stays withheld — it was generated, not
            # sourced — but the caution is a caution, not a refusal.
            lines.append(
                "- No listing is asserted for this species, but the status is "
                "unverified. Our stored angling notes are withheld as unsourced; "
                "tell the angler to confirm the current listing before targeting."
            )
    for label, field in (
        ("conservation status", ctx.conservation_status),
        ("habitat", ctx.habitat_note),
        ("angling note", ctx.angling_note),
        ("native to Ontario", ctx.native_to_ontario),
    ):
        line = _line(label, field)
        if line:
            lines.append(line)
    return "\n".join(lines)


def render_explore(response: ExploreResponse) -> str:
    if not response.results:
        reason = response.empty_reason.value if response.empty_reason else "no candidates"
        return f"### Exploration candidates\nNone ({reason})."

    lines = ["### Exploration candidates", f"_{response.scoring_note}_", ""]
    for i, r in enumerate(response.results, 1):
        name = r.name or f"unnamed segment {r.ogf_id}"
        bits = [f"score {r.score:.3f}"]
        if r.stream_order is not None:
            bits.append(f"order {r.stream_order}")
        bits.append(f"pressure {r.observation_pressure:.2f}")
        # Same call every other value in the layer gets. The three access
        # states are ordinary ContextField states, so their wording comes from
        # _EMPTY_PHRASING rather than from three branches invented here.
        bits.append(f"access {r.access.explain()}")
        if r.is_confluence:
            bits.append("confluence")
        lines.append(f"{i}. {name} ({r.lat:.4f}, {r.lng:.4f}) — {', '.join(bits)}")

    total = len(response.results)
    if response.results_on_placeholder_access:
        lines.append(
            f"\n_Access is unmapped for {response.results_on_placeholder_access} "
            f"of these {total}: nobody has surveyed roads or parking that far "
            f"out, so there is no figure to give. What ranked them — how little "
            f"has been reported there, the structure, the remoteness — is real._"
        )
    if response.results_with_unknown_access_coverage:
        # A gap in our pipeline, not in the world. Different remedy, so it gets
        # different words; the shell command that fixes it belongs in the log,
        # not in something an angler reads.
        lines.append(
            f"\n_For {response.results_with_unknown_access_coverage} of these "
            f"{total}, the stored access figures predate the record of where "
            f"access was actually surveyed, so it cannot be said whether they "
            f"mean anything. Recomputing access scores would settle it._"
        )

    if response.tied_at_top > len(response.results):
        # Without a habitat term the surviving signals are coarse, so ties are
        # large. Presenting ten of a thousand equally-ranked segments as "the
        # best" would be a precision claim the score cannot support.
        lines.append(
            f"\n_{response.tied_at_top} candidates share the top score — the "
            f"ordering within that group is arbitrary, not a ranking._"
        )
    if response.excluded_count:
        lines.append(
            f"\n_{response.excluded_count} candidate(s) excluded by the "
            f"physical-plausibility gate._"
        )
        for ex in response.excluded_examples:
            lines.append(f"  - {ex}")
    return "\n".join(lines)


def render_user_layer(layer: UserLayer) -> str:
    """The derived layer, with the epistemic rule visible in the output.

    Patterns that lack a comparison set are still printed, marked as not yet
    claimable. Hiding them would leave the caller unable to say "one session
    is a hypothesis, not a rule" — which is more useful than silence.
    """
    if layer.total_stops == 0:
        return "### This angler\nNo trips logged yet."

    lines = [
        "### This angler",
        f"- {layer.total_sessions} session(s), {layer.total_stops} stop(s)",
    ]
    if layer.blank_rate is not None:
        lines.append(f"- blank rate: {layer.blank_rate:.0%}")
    lines += _profile_lines(layer.profile)
    if layer.target_species:
        lines.append(
            f"- appears to target (from logs, not configured): {', '.join(layer.target_species)}"
        )
    if layer.species_logged:
        lines.append(f"- species logged: {', '.join(layer.species_logged)}")

    if layer.patterns:
        lines.append("- personal patterns:")
        for p in layer.patterns:
            mark = "claimable" if p.is_claimable else "NOT yet claimable"
            lines.append(
                f"  - {p.statement} (n={p.sample_size} vs {p.comparison_size}, "
                f"{p.confidence} confidence, {mark})"
            )
    if layer.known_gaps:
        lines.append("- known gaps:")
        for g in layer.known_gaps:
            lines.append(f"  - {g}")
    return "\n".join(lines)


def _profile_lines(profile: AnglerProfile | None) -> list[str]:
    """The angler profile as register guidance, never as a level.

    Orientation decides emphasis, not content; the concept states decide
    wording. Familiar concepts can be named outright, unseen ones need
    explaining. There is no overall beginner/expert label to print.
    """
    if profile is None:
        return []
    o = profile.orientation
    lines = [
        f"- leaning (0 = specialist, 1 = lifelister, from logs): {o.value:.2f}, "
        f"confidence {o.confidence:.2f}",
        f"- records sizes on {profile.size_focus.value:.0%} of catches (soft); "
        f"small-species share {profile.micro_focus.value:.0%} (soft)",
    ]
    by_state: dict[str, list[str]] = {"familiar": [], "introduced": []}
    for c in profile.concepts.values():
        if c.state in by_state and terms.TERMS[c.term_id]["mapped_from"] is not None:
            by_state[c.state].append(terms.TERMS[c.term_id]["technical"].lower())
    if by_state["familiar"]:
        lines.append(f"- map words they know (say plainly): {', '.join(by_state['familiar'])}")
    if by_state["introduced"]:
        lines.append(
            f"- map words they have met (define briefly): {', '.join(by_state['introduced'])}"
        )
    lines.append("- any other map word: explain it in plain words first")
    return lines


def render_species_history(history: SpeciesHistory) -> str:
    """The angler's own record with one species.

    Catches and blanks both, and the blank count arrives with its caveat
    attached rather than as a bare number the reader will over-interpret.
    """
    if history.caught_stops == 0 and history.blank_stops == 0 and not history.insights:
        reason = history.empty_reason.value if history.empty_reason else "nothing logged"
        return f"### Your record with {history.species}\nNothing logged ({reason})."

    lines = [f"### Your record with {history.species}"]
    if history.caught_stops:
        lines.append(f"- caught on {history.caught_stops} stop(s)")
        if history.last_caught:
            lines.append(f"- last caught: {history.last_caught}")
        if history.locations:
            lines.append(f"- where: {', '.join(history.locations)}")
        lines.append("- what worked:")
        for setup in history.productive_setups:
            lines.append(f"  - {setup}")
    else:
        lines.append("- never logged a catch of this species")

    lines.append(
        f"- {history.blank_stops} blank stop(s) in the log overall — targeting is "
        f"not always recorded, so this is an upper bound on 'tried and failed', "
        f"not a count of it"
    )

    if history.insights:
        lines.append("- stored insights:")
        for ins in history.insights:
            line = f"  - [{ins.confidence}] {ins.conclusion} [{ins.provenance.describe()}]"
            lines.append(line)
            if ins.recommendation:
                lines.append(f"    recommendation: {ins.recommendation}")
    return "\n".join(lines)


# -- one stop from the user's own log ------------------------------------------

# Fields worth showing an angler about their own stop. Everything else on the
# row — user_id, session_id, row ids, photo paths and EXIF coordinates — is
# plumbing, and there is no reason for any of it to reach a prompt.
_STOP_FIELDS: tuple[tuple[str, str], ...] = (
    ("technique", "technique"),
    ("gear", "gear"),
    ("water_level", "water level"),
    ("water_clarity", "clarity"),
    ("weather_notes", "weather"),
    ("time_of_day", "time of day"),
)


def render_logged_stop(stop) -> str:
    """One logged stop, as a sentence rather than a database row.

    `json.dumps(stop)` used to go straight into a prompt here. That put the
    whole row in front of the model — internal ids, photo paths, the EXIF
    coordinates of a catch photo — and gave it no idea which parts were the
    angler's observations and which were bookkeeping. Unrecorded fields are
    named as unrecorded rather than omitted, because "we did not write it
    down" and "it was not the case" are different facts.
    """
    get = stop.get if hasattr(stop, "get") else lambda k, d=None: getattr(stop, k, d)

    where = get("location_name") or get("location_text") or "an unnamed spot"
    when = get("date") or get("date_approx") or "an undated trip"

    caught = get("species_caught")
    if isinstance(caught, str):
        import json as _json

        try:
            caught = _json.loads(caught)
        except (TypeError, ValueError):
            caught = []
    caught = [str(c) for c in (caught or [])]

    head = f"{where} ({when}): "
    head += f"caught {', '.join(caught)}" if caught else "caught nothing"

    details = [
        f"{label} {get(field)}" if get(field) else f"{label} unrecorded"
        for field, label in _STOP_FIELDS
    ]
    line = head + " — " + ", ".join(details)

    notes = get("notes")
    if notes:
        line += f'\n  their note: "{str(notes)[:200]}"'
    return line


def render_recorded_insight(insight: RecordedInsight) -> str:
    """A stored insight with the source it came from attached.

    An insight the assistant concluded in an earlier session and one drawn
    from a survey are not the same kind of claim, and anything reasoning over
    one needs to be able to tell which it has.
    """
    line = f"[{insight.confidence}] {insight.conclusion} [{insight.provenance.describe()}]"
    if insight.recommendation:
        line += f"\n  recommendation: {insight.recommendation}"
    return line


# -- the spot card ---------------------------------------------------------------
#
# Everything a person reads on a tapped spot is produced here, from the Spot, the
# describe() slices and the static dictionary in translate.py. No model call: a
# map tap is free and the card cannot drift between runs. The fixed strings are
# constants so a lint test can read them all.

CARD_TITLES = {
    "hook": "Why it's interesting",
    "might_find": "You might find",
    "why_gather": "Why fish gather here",
    "getting_there": "Getting there",
}
CARE_MESSAGE = (
    "Protected fish have been recorded near here. Handle any fish with care and release it."
)
HONEST_EMPTY = "No fish records within {radius} km, so this is a place to find out."
HONEST_EMPTY_IMPRECISE = (
    "Fish records near here are too fuzzy to place, so this is a place to find out."
)
MIGHT_FIND_CAVEAT = "Recorded nearby, not a catch guarantee."
NO_RECORDS = "No fish records within {radius} km."
NO_OTHER_RECORDS = "No other fish records within {radius} km."
NO_RECORDS_TO_CHECK = "We hold no fish records for this area yet."
FEW_LOOKERS = (
    "That says nothing about whether fish are there; it may only mean few people have looked."
)
PROTECTED_NOTE = "Protected species recorded nearby are not listed."
IMPRECISE_NOTE = "Records with a deliberately fuzzy location are left out."
FACTS_TITLE = "What our records say about this spot"
UNVERIFIED_TITLE = "Not checked on the ground"
NO_ACCESS = "No parking, trailhead or boat launch found within {radius} km in our map data."
OLDER_RECORD = "Older record"
UNVERIFIED_ALWAYS = "Fishing rules and permission to cross private land are not checked here."
UNVERIFIED_OSM = (
    "Parking, trails, launches and road crossings come from OpenStreetMap, which "
    "volunteers map. A missing one may just be unmapped."
)
UNVERIFIED_DISTANCE = "Distances are in a straight line, not by road or trail."
UNVERIFIED_DAM = (
    "We could not confirm which side of the {kind} the water runs away from, so the "
    "spot sits at the {kind} itself."
)
CROWN_MAPPED = (
    "Crown land is mapped within {radius} km. Public access is generally allowed, "
    "but check local rules before you go."
)
CROWN_NONE = "No Crown land found within {radius} km in our data."
CROWN_UNCHECKED = "We have no Crown land data for this area."
SPECIAL_TITLE = "Recorded more often here"
# Never "lives here": records are presence-only and follow observers as much as fish.
SPECIAL_LABEL = "Recorded more often here than along the rest of the {river}"
SPECIAL_HOOK = "{name} is recorded more often here than along the rest of the {river}."
SPECIAL_HOOK_MORE = (
    "{name} and {n} more are recorded more often here than along the rest of the {river}."
)
SPECIAL_LINE = (
    "{here} of {here_total} records here ({here_pct}%), against {else_pct}% "
    "along the rest of the {river}"
)
SPECIAL_LINE_NONE_ELSEWHERE = (
    "{here} of {here_total} records here ({here_pct}%), and none along the rest of the {river}"
)
SPECIAL_BASIS = {
    "survey": "Counted from fish surveys only, not photos, with each fish counted once per survey.",
    "sighting": (
        "Counted from photo and sighting records only, one for each fish per person per day."
    ),
}
SPECIAL_CAVEAT = (
    "This compares how often each fish was recorded. Records follow where people look "
    "as well as where fish are, so it does not mean the fish are found only here."
)
TOP_SPECIES = 5
_SOURCE_OHN = "Ontario Hydro Network"
_SOURCE_OSM = "OpenStreetMap"
_SOURCE_CURATION = "Fishdex's own list of rivers"
_YOUR_CATCH = "your logged catch"


@dataclass
class _Species:
    """One recorded species as the card ranks it, before it becomes a CardSpecies."""

    line: CardSpecies
    count: int
    scientific: str  # what the record filed it under, for matching against a user's catches
    common: str | None


@dataclass
class _CardInputs:
    spot: Spot
    ctx: PlaceContext | None
    radius_km: float
    shown: list[CardSpecies]  # every species that may be named, ranked
    shown_records: int  # records behind `shown`
    n_protected: int
    n_imprecise: int
    # Species in `shown` the logged-in user has not caught, ranked like `shown`.
    # None for a caller with no user; [] for a user with nothing new.
    new_species: list[CardSpecies] | None = None
    # Different species the user has confirmed catching. The "new to you" hook
    # stays quiet below `MIN_SPECIES_FOR_HOOK`.
    caught_species: int = 0
    special: SpecialHere | None = None

    @property
    def radius(self) -> str:
        return f"{self.radius_km:g}"


class HookRule(NamedTuple):
    """One rule in the hook chain. `build` is None for a slot not yet implemented."""

    kind: str
    build: Callable[[_CardInputs], str | None] | None


def _hook_protected(c: _CardInputs) -> str | None:
    return CARE_MESSAGE if c.n_protected else None


def _hook_new_to_you(c: _CardInputs) -> str | None:
    """ "3 species recorded here you haven't caught yet": only for a user with something new,
    and only once they have caught enough different species for "new" to mean something.
    Before that the chain falls through to the card's sentence about the water."""
    if not c.new_species or c.caught_species < MIN_SPECIES_FOR_HOOK:
        return None
    return spot_labels.new_to_you_hook(len(c.new_species))


def _hook_structure(c: _CardInputs) -> str | None:
    """One plain sentence about the water, only where a record supports it."""
    attrs = c.spot.structure_attrs
    if c.spot.kind == "creek_junction":
        sizes = _junction_sizes(attrs)
        if sizes:
            return (
                f"Two streams meet here, one fed by about {_km(sizes[0])} km of channel "
                f"upstream and the other by about {_km(sizes[1])} km."
            )
    elif c.spot.kind == "river_mouth" and attrs.get("lake"):
        return f"A river running out into {attrs['lake']}."
    elif c.spot.kind == "below_dam" and attrs.get("dam_kind") and attrs.get("tailwater_verified"):
        return f"The water just downstream of a {attrs['dam_kind']}."
    return None


def _display_name(common_name: str | None, species: str) -> str:
    name = common_name or species
    return name.title() if name == name.lower() else name


def _hook_distinctive(c: _CardInputs) -> str | None:
    """A species recorded clearly more often here than along the rest of the river."""
    sp = c.special
    if sp is None or sp.status != "found" or not sp.species:
        return None
    first = _display_name(sp.species[0].common_name, sp.species[0].species)
    river = sp.river or "river"
    if len(sp.species) == 1:
        return SPECIAL_HOOK.format(name=first, river=river)
    return SPECIAL_HOOK_MORE.format(name=first, n=len(sp.species) - 1, river=river)


def _special_block(c: _CardInputs) -> SpecialHereBlock | None:
    sp = c.special
    if sp is None or sp.status != "found" or not sp.species or sp.evidence is None:
        return None
    river = sp.river or "river"
    lines = []
    for s in sp.species:
        here_pct = round(100 * s.here_share)
        if s.elsewhere_records:
            text = SPECIAL_LINE.format(
                here=s.here_records,
                here_total=s.here_total,
                here_pct=here_pct,
                else_pct=round(100 * s.elsewhere_share),
                river=river,
            )
        else:
            text = SPECIAL_LINE_NONE_ELSEWHERE.format(
                here=s.here_records, here_total=s.here_total, here_pct=here_pct, river=river
            )
        common = _display_name(s.common_name, s.species)
        lines.append(
            SpecialHereSpeciesLine(
                name=common,
                scientific_name=s.species if s.species.lower() != common.lower() else None,
                text=text,
                here_records=s.here_records,
                here_total=s.here_total,
                elsewhere_records=s.elsewhere_records,
                elsewhere_total=s.elsewhere_total,
            )
        )
    return SpecialHereBlock(
        title=SPECIAL_TITLE,
        label=SPECIAL_LABEL.format(river=river),
        species=lines,
        evidence=sp.evidence,
        basis_text=SPECIAL_BASIS[sp.evidence],
        caveat=SPECIAL_CAVEAT,
        radius_km=sp.radius_km,
    )


def _hook_honest_empty(c: _CardInputs) -> str | None:
    if c.shown or c.n_protected:
        return None
    if c.n_imprecise:
        return HONEST_EMPTY_IMPRECISE
    return HONEST_EMPTY.format(radius=c.radius)


# First rule that has data wins. "new_to_you" is per-user: it speaks only when
# the caller is a logged-in user with a recorded species they have not caught
# and at least `MIN_SPECIES_FOR_HOOK` species caught, and otherwise the chain
# falls through to "structure". "distinctive" needs
# the special-here line: frequency here against the rest of the river section.
HOOK_CHAIN: tuple[HookRule, ...] = (
    HookRule("protected_species", _hook_protected),
    HookRule("new_to_you", _hook_new_to_you),
    HookRule("distinctive", _hook_distinctive),
    HookRule("structure", _hook_structure),
    HookRule("honest_empty", _hook_honest_empty),
)


def _choose_hook(c: _CardInputs) -> CardHook:
    for rule in HOOK_CHAIN:
        text = rule.build(c) if rule.build else None
        if text:
            return CardHook(title=CARD_TITLES["hook"], kind=rule.kind, text=text)
    # Records exist, but nothing above has anything specific to say: say what
    # the kind of place is rather than leave the block blank.
    return CardHook(
        title=CARD_TITLES["hook"],
        kind="kind_description",
        text=spot_labels.KINDS[c.spot.kind]["one_liner"],
    )


def _km(value: float) -> str:
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".removesuffix(".0")


def _junction_sizes(attrs: dict) -> tuple[float, float] | None:
    """(smaller, larger) upstream channel length at a junction, when both are known."""
    joining = float(attrs.get("joining_upstream_km") or 0)
    stretch = float(attrs.get("stretch_upstream_km") or 0)
    if joining <= 0 or stretch <= 0:
        return None
    return (min(joining, stretch), max(joining, stretch))


def _distance(metres: int) -> str:
    if metres >= 1000:
        return f"about {metres / 1000:.1f} km"
    return f"about {max(10, round(metres, -1))} m"


def _species_line(rec) -> _Species | None:
    prov = rec.survey_record or rec.provenance
    if prov.kind.value != "record" or prov.source == _YOUR_CATCH:
        return None  # a card is the same for everyone and never leans on web or personal data
    common = rec.common_name or rec.species
    if common == common.lower():
        common = common.title()
    scientific = rec.species if rec.species.lower() != common.lower() else None
    note = OLDER_RECORD if prov.date and _is_stale(prov.date, None) else None
    return _Species(
        line=CardSpecies(
            name=common,
            scientific_name=scientific,
            source=prov.source or "record",
            date=prov.date,
            evidence="survey" if rec.survey_record else "sighting",
            cite=prov.describe(),
            note=note,
        ),
        count=rec.count,
        scientific=rec.species,
        common=rec.common_name,
    )


def _rank(rows: list[_Species]) -> list[_Species]:
    """Survey records first, then the most recent, then the most often recorded."""
    rows = sorted(rows, key=lambda r: r.line.name.lower())
    rows.sort(key=lambda r: -r.count)
    rows.sort(key=lambda r: r.line.date or "", reverse=True)
    rows.sort(key=lambda r: 0 if r.line.evidence == "survey" else 1)
    return rows


def _might_find(c: _CardInputs) -> MightFindBlock:
    top = c.shown[:TOP_SPECIES]
    left = len(c.shown) - len(top)
    empty = None
    if not c.shown:
        reason = c.ctx.records.empty_reason if c.ctx and c.ctx.records else None
        if c.n_protected:
            empty = NO_OTHER_RECORDS.format(radius=c.radius)
        elif reason is not None and reason.value == "source_does_not_cover_area":
            empty = NO_RECORDS_TO_CHECK
        else:
            empty = f"{NO_RECORDS.format(radius=c.radius)} {FEW_LOOKERS}"
    notes = [
        n for n, flag in ((PROTECTED_NOTE, c.n_protected), (IMPRECISE_NOTE, c.n_imprecise)) if flag
    ]
    return MightFindBlock(
        title=CARD_TITLES["might_find"],
        species=top,
        more_text=(
            f"and {left} more species recorded nearby"
            if left > 1
            else "and 1 more species recorded nearby"
            if left == 1
            else None
        ),
        caveat=MIGHT_FIND_CAVEAT,
        empty_text=empty,
        note=" ".join(notes) or None,
        radius_km=c.radius_km,
        # Not shown when the care message is: a protected species nearby replaces
        # everything else on the card, and the share of the rest would only hint at it.
        special_here=None if c.n_protected else _special_block(c),
    )


def _why_gather(c: _CardInputs) -> WhyGatherBlock:
    kind = c.spot.kind
    general = translate.why_fish_gather(kind)
    facts = _spot_facts(c)
    return WhyGatherBlock(
        title=CARD_TITLES["why_gather"],
        what_it_is=spot_labels.KINDS[kind]["one_liner"],
        general=GeneralReasoning(
            text=str(general.value),
            label=general.meaning or translate.GENERAL_REASONING_LABEL,
        ),
        facts_title=FACTS_TITLE,
        facts=facts,
    )


def _spot_facts(c: _CardInputs) -> list[CardFact]:
    """What the data says about this spot, one fact per line, each tagged with its source."""
    spot, attrs = c.spot, c.spot.structure_attrs
    facts: list[CardFact] = []
    if spot.kind == "creek_junction":
        sizes = _junction_sizes(attrs)
        if sizes:
            facts.append(
                CardFact(
                    text=(
                        f"The smaller stream has about {_km(sizes[0])} km of channel upstream "
                        f"of this point; the larger has about {_km(sizes[1])} km."
                    ),
                    source=_SOURCE_OHN,
                )
            )
    elif spot.kind == "river_mouth" and attrs.get("lake"):
        facts.append(
            CardFact(text=f"The river runs out into {attrs['lake']}.", source=_SOURCE_CURATION)
        )
    elif spot.kind == "below_dam":
        kind = attrs.get("dam_kind")
        if kind:
            facts.append(CardFact(text=f"Mapped as a {kind}.", source=_SOURCE_OSM))
        if attrs.get("operator"):
            facts.append(CardFact(text=f"Run by {attrs['operator']}.", source=_SOURCE_OSM))
    elif spot.kind == "public_fishing":
        facts.append(CardFact(text="Marked on the map as a place to fish.", source=_SOURCE_OSM))

    link = c.ctx.structure.waterbody_connection if c.ctx and c.ctx.structure else None
    if link is not None and link.value is True:
        facts.append(
            CardFact(text="The water here connects to a lake or pond.", source=_SOURCE_OHN)
        )

    if c.shown:
        sources = sorted({_source_name(s.source) for s in c.shown})
        facts.append(
            CardFact(
                text=(
                    f"{c.shown_records} fish records within {c.radius} km, "
                    f"covering {len(c.shown)} species."
                ),
                source=", ".join(sources),
            )
        )
    return facts


def _source_name(source: str) -> str:
    return source.split("/", 1)[0]


def _access_details(ref: AccessRef) -> list[str]:
    a = ref.attributes
    out: list[str] = []
    if str(a.get("access", "")).lower() in {"yes", "public", "permissive"}:
        out.append("mapped as open to the public")
    elif str(a.get("access", "")).lower() in {"private", "no", "customers"}:
        out.append(f"mapped as {'for customers only' if a['access'] == 'customers' else 'private'}")
    if str(a.get("fee", "")).lower() == "no":
        out.append("mapped as free")
    elif str(a.get("fee", "")).lower() == "yes":
        out.append("mapped as paid")
    if str(a.get("capacity", "")).isdigit():
        out.append(f"about {a['capacity']} spaces")
    if a.get("surface"):
        out.append(str(a["surface"]))
    if a.get("operator"):
        out.append(f"run by {a['operator']}")
    return out


def _access_item(ref: AccessRef) -> AccessItem:
    label = spot_labels.ACCESS_LABELS[ref.kind]
    base = "Public road crossing" if ref.kind == "road_crossing" else label
    named = f"{base} ({ref.name})" if ref.name else base
    return AccessItem(
        kind=ref.kind,
        label=label,
        text=f"{named} {_distance(ref.distance_m)} away",
        distance_m=ref.distance_m,
        details=_access_details(ref),
    )


def _crown_land(c: _CardInputs) -> CrownLandFlag:
    field = c.ctx.access.crown_land if c.ctx and c.ctx.access else None
    if field is not None and not field.is_empty:
        return CrownLandFlag(
            state="mapped",
            text=CROWN_MAPPED.format(radius=c.radius),
            source=field.provenance.source if field.provenance else None,
        )
    if (
        field is not None
        and field.empty_reason
        and field.empty_reason.value == "no_records_in_radius"
    ):
        return CrownLandFlag(state="none_found", text=CROWN_NONE.format(radius=c.radius))
    return CrownLandFlag(state="not_checked", text=CROWN_UNCHECKED)


def _getting_there(c: _CardInputs) -> GettingThereBlock:
    spot = c.spot
    nearest: dict[str, AccessRef] = {}
    for ref in sorted(
        spot.getting_there + spot.bridges, key=lambda r: (r.distance_m, r.source_ref)
    ):
        nearest.setdefault(ref.kind, ref)
    items = [
        _access_item(nearest[k])
        for k in ("parking", "trail_head", "boat_launch", "road_crossing")
        if k in nearest
    ]

    unverified = [UNVERIFIED_OSM]
    if items:
        unverified.append(UNVERIFIED_DISTANCE)
    if spot.kind == "below_dam" and spot.structure_attrs.get("tailwater_verified") is False:
        unverified.append(UNVERIFIED_DAM.format(kind=spot.structure_attrs.get("dam_kind", "dam")))
    unverified.append(UNVERIFIED_ALWAYS)

    return GettingThereBlock(
        title=CARD_TITLES["getting_there"],
        items=items,
        empty_text=None if items else NO_ACCESS.format(radius="1.5"),
        crown_land=_crown_land(c),
        unverified_title=UNVERIFIED_TITLE,
        unverified=unverified,
    )


def _terms_used(*texts: str) -> list[GlossaryTerm]:
    """Registry entries for the hard words that actually appear in this card's text."""
    body = " ".join(texts)
    out = []
    for term_id in terms.CARD_LINKED_TERMS:
        if re.search(rf"\b{term_id}\b", body, re.IGNORECASE):
            out.append(
                GlossaryTerm(term=term_id, term_id=term_id, text=terms.TERMS[term_id]["definition"])
            )
    return out


def _rank_recorded(
    ctx: PlaceContext | None, protected: frozenset[str]
) -> tuple[list[_Species], int, int]:
    """(ranked nameable species, n_protected, n_imprecise) for a describe() result.

    The one place that decides which recorded species a card may name, so the
    species list, the "new to you" hook and the `new_to_me` filter cannot disagree.
    """
    rows: list[_Species] = []
    n_protected = n_imprecise = 0
    for rec in ctx.records.species if ctx and ctx.records else []:
        if rec.species.strip().lower() in protected:
            n_protected += 1
        elif rec.is_obscured and not rec.survey_record:
            n_imprecise += 1  # ~22 km of fuzz is not "within 2 km"
        elif (row := _species_line(rec)) is not None:
            rows.append(row)
    return _rank(rows), n_protected, n_imprecise


def new_species_for_user(
    ctx: PlaceContext | None, protected: frozenset[str], caught: frozenset[str]
) -> list[CardSpecies]:
    """Nameable recorded species the user has not caught, ranked. Never a protected one."""
    ranked, _, _ = _rank_recorded(ctx, protected)
    return [r.line for r in ranked if not is_caught(caught, r.scientific, r.common)]


def _new_to_you_block(new_species: list[CardSpecies] | None) -> NewToYouBlock | None:
    if not new_species:
        return None
    return NewToYouBlock(
        title=spot_labels.NEW_TO_YOU_TITLE,
        count=len(new_species),
        tap_prompt=spot_labels.NEW_TO_YOU_TAP_PROMPT,
        species=new_species,
        note=spot_labels.NEW_TO_YOU_NOTE,
    )


def render_spot(
    spot: Spot,
    ctx: PlaceContext | None,
    protected: frozenset[str] = frozenset(),
    unchecked: int = 0,
    caught: frozenset[str] | None = None,
    special: SpecialHere | None = None,
    caught_species: int = 0,
) -> SpotCard:
    """The whole tap card for one spot: four blocks, every string already written.

    `ctx` is `describe(..., caller="map_tap", radius_km=2)` for the spot. It is
    the only source of fish records, so a card can never say more than the
    corpus holds. `protected` names the recorded species (lowercased scientific
    names) that carry an affirmative listing signal; the caller decides that
    with `status_known_listed`, never `sar_alert`. Those species are not named
    anywhere in the card, are not counted, and turn the hook into a care message.
    `unchecked` is how many recorded species were not in the species file.
    `special` is the frequency comparison for the spot's river section
    (context/special.py); it fills the "distinctive" hook and `might_find.special_here`
    only when it found something, and the protected-species care message still comes first.

    The card is the same for every user, with one exception. `caught` is the
    match keys of the logged-in user's confirmed catches (`new_to_you.caught_keys`)
    and is None for a caller with no user. Only then is the "new to you" block
    filled, and the hook too once `caught_species` (how many different species they
    have confirmed) reaches `MIN_SPECIES_FOR_HOOK`. `ctx` is never built from a
    person's history, so nothing else on the card changes with who asks.
    """
    radius = ctx.place.radius_km if ctx else 2.0
    ranked, n_protected, n_imprecise = _rank_recorded(ctx, protected)
    new_species = (
        [r.line for r in ranked if not is_caught(caught, r.scientific, r.common)]
        if caught is not None
        else None
    )
    inputs = _CardInputs(
        spot=spot,
        ctx=ctx,
        radius_km=radius,
        shown=[r.line for r in ranked],
        shown_records=sum(r.count for r in ranked),
        n_protected=n_protected,
        n_imprecise=n_imprecise,
        new_species=new_species,
        caught_species=caught_species,
        special=special,
    )
    hook = _choose_hook(inputs)
    getting_there = _getting_there(inputs)
    why_gather = _why_gather(inputs)
    return SpotCard(
        spot_id=spot.spot_id,
        hook=hook,
        might_find=_might_find(inputs),
        why_gather=why_gather,
        getting_there=getting_there,
        new_to_you=_new_to_you_block(new_species),
        caveats=[CardCaveat(**c) for c in spot_labels.caveats_for(spot.kind, spot.signals)],
        terms=_terms_used(spot.title, hook.text, *(f.text for f in why_gather.facts)),
        meta=CardMeta(
            records_radius_km=radius,
            species_shown=len(ranked),
            species_withheld_protected=n_protected,
            species_withheld_imprecise=n_imprecise,
            species_status_unchecked=unchecked,
            special_here=special.status if special else "not_applicable",
            cache_key=f"{spot.spot_id}@{spot.built_at}",
        ),
    )
