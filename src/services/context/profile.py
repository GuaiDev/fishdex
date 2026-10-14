"""Derive the angler profile from logged behaviour.

Called from the derived-layer recompute, so it runs once per logged session.
Groundwork: every weight and threshold below is a named constant chosen by
reasoning, not tuned against anyone's data, and is expected to change once
onboarding exists.

**Orientation (0 = specialist, 1 = lifelister).** Each session is scored for
lifelist evidence and specialist evidence; the session's signal is the
lifelist share of the two. Intent counts over outcome:

  * lifelist: catching a species new to the dex *that was recorded at that
    place* (intended), going to new water where species are recorded that are
    in neither the dex nor any water visited before (novelty, normalised by
    `local_richness`); a new species nothing recorded there counts for little
    (accidental).
  * specialist: returning to the same water and catching a species caught
    there before; returning at all; catching a species already caught in an
    earlier, different season.

Raw species count is never used. Lifelist evidence is scaled by the richness
confidence of the place, so thin local data lowers what a session can say.
Sessions are combined with a decay-weighted average plus a neutral prior, so
a few trips barely move it, and confidence grows with accumulated weight.

**Size focus / micro focus.** Separate soft values, not on the spectrum:
the decayed share of confirmed catches that carry a size, and of catches
that are small-bodied (microfishing) taxa.

**Literacy, per concept.** Evidence per term_id: logged near a feature of
that type, saved a pin near one, opened its definition, seen it on the map.
unseen -> introduced -> familiar by the thresholds below. Never one level.

Only confirmed catches are read — the same rule as the dex — so the profile
never learns from a parser's or a photo model's guess.
"""

import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlite_utils import Database

from src.models.angler_profile import (
    CONCEPT_KEY_PREFIX,
    CONCEPT_STATE_VALUE,
    AnglerProfile,
    BlendedValue,
    ConceptEvidence,
    ConceptState,
    ProfileValue,
    SessionEvidence,
)
from src.models.context import LocalRichness
from src.services import terms
from src.services.context.richness import local_richness
from src.services.species_mapping import COMMON_TO_SCIENTIFIC
from src.storage import angler_profile as store

# ── spectrum weights ──────────────────────────────────────────────────────────
LIFER_INTENDED_WEIGHT = 1.0  # new to the dex, and recorded at that place
LIFER_UNKNOWN_WEIGHT = 0.5  # new to the dex, place too thinly recorded to tell
LIFER_ACCIDENTAL_WEIGHT = 0.2  # new to the dex, not recorded there
NOVELTY_TRAVEL_WEIGHT = 1.0  # x share of the place's recorded species that are new options
REPEAT_WATER_SPECIES_WEIGHT = 1.0  # same water, a species caught there before
REPEAT_WATER_WEIGHT = 0.4  # same water again, whatever the outcome
SEASON_SPAN_WEIGHT = 0.8  # a species caught before in a different season
# A new species at a place with no species records at all: the lifer still
# counts, at this fraction, since nothing says whether it was sought.
UNRECORDED_PLACE_CONFIDENCE = 0.25
SESSION_EVIDENCE_CAP = 1.0  # one session's weight never exceeds this
SPECTRUM_HALF_LIFE_SESSIONS = 20  # older sessions count half after this many
NEUTRAL_PRIOR_WEIGHT = 8.0  # pseudo-sessions at 0.5: two strong trips move it ~0.1
CONFIDENCE_SCALE = 8.0  # accumulated weight at which confidence is ~0.63

# ── side values ───────────────────────────────────────────────────────────────
SIDE_HALF_LIFE_SESSIONS = 20
SIDE_PRIOR_WEIGHT = 3.0
SIDE_PRIOR_VALUE = 0.0  # no evidence of either focus until it is logged
MICRO_MARKERS: tuple[str, ...] = (
    "darter",
    "dace",
    "madtom",
    "shiner",
    "minnow",
    "chub",
    "sculpin",
    "lamprey",
    "stickleback",
    "logperch",
    "mudminnow",
    "killifish",
    "topminnow",
    "silverside",
)

# ── declared values ───────────────────────────────────────────────────────────
DECLARED_HALF_LIFE_SESSIONS = 10  # a questionnaire answer counts half after this many
DECLARED_CONCEPT_MIN_WEIGHT = 0.5  # below this effective weight a declared state lapses

# ── same water ────────────────────────────────────────────────────────────────
# Pin link first, then OHN segment, then a ~300 m grid cell.
SAME_WATER_GRID_DEG = 0.003

# ── literacy ──────────────────────────────────────────────────────────────────
NEAR_FEATURE_M = 1000.0
FAMILIAR_LOGGED_SESSIONS = 3
FAMILIAR_SEEN_COUNT = 50
INTRODUCED_SEEN_COUNT = 5

_SEASONS = {
    12: "winter",
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "spring",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "fall",
    10: "fall",
    11: "fall",
}
_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def derive_profile(db: Database, user_id: int) -> AnglerProfile:
    """The inferred profile for one user. Declared values are blended at read
    time by `blend()`, so a settings override takes effect without a recompute."""
    sessions = _sessions(db, user_id)
    evidence, orientation = _orientation(db, sessions)
    size, micro = _side_values(sessions)
    concepts = _concepts(db, user_id, sessions)
    return AnglerProfile(
        user_id=user_id,
        orientation=orientation,
        size_focus=size,
        micro_focus=micro,
        concepts=concepts,
        sessions_used=len(sessions),
        session_evidence=evidence,
        computed_at=datetime.now(UTC).isoformat(),
    )


def inferred_rows(profile: AnglerProfile) -> dict[str, tuple[float, float]]:
    """The profile's inferred values as `{key: (value, weight)}` for storage."""
    rows = {}
    for key in ("orientation", "size_focus", "micro_focus"):
        v = getattr(profile, key).inferred
        if v is not None:
            rows[key] = (v.value, v.weight)
    for term_id, c in profile.concepts.items():
        weight = float(sum(e.count for e in c.evidence))
        rows[CONCEPT_KEY_PREFIX + term_id] = (CONCEPT_STATE_VALUE[c.state], weight)
    return rows


def blend(db: Database, profile: AnglerProfile) -> AnglerProfile:
    """Fold this user's declared values into an inferred profile.

    A declared value acts as a prior whose weight halves every
    `DECLARED_HALF_LIFE_SESSIONS` sessions logged after it, so a questionnaire
    answer dominates at first and behaviour takes over. An override is used as
    is. Nothing is filtered: the inferred part is still carried alongside.
    """
    declared = {
        k: v["declared"]
        for k, v in store.get_values(db, profile.user_id).items()
        if "declared" in v
    }
    if not declared:
        return profile
    out = profile.model_copy(deep=True)
    for key in ("orientation", "size_focus", "micro_focus"):
        d = declared.get(key)
        if d is None:
            continue
        cur: BlendedValue = getattr(out, key)
        prior_value = 0.5 if key == "orientation" else SIDE_PRIOR_VALUE
        prior_weight = NEUTRAL_PRIOR_WEIGHT if key == "orientation" else SIDE_PRIOR_WEIGHT
        if d.is_override:
            setattr(
                out,
                key,
                BlendedValue(value=d.value, confidence=1.0, inferred=cur.inferred, declared=d),
            )
            continue
        d_w = d.weight * _decay(
            store.sessions_since(db, profile.user_id, d.updated_at), DECLARED_HALF_LIFE_SESSIONS
        )
        inf = cur.inferred
        inf_v, inf_w = (inf.value, inf.weight) if inf else (prior_value, 0.0)
        value = (prior_value * prior_weight + d.value * d_w + inf_v * inf_w) / (
            prior_weight + d_w + inf_w
        )
        setattr(
            out,
            key,
            BlendedValue(
                value=_clip(value), confidence=_confidence(d_w + inf_w), inferred=inf, declared=d
            ),
        )
    for key, d in declared.items():
        if not key.startswith(CONCEPT_KEY_PREFIX):
            continue
        term_id = key[len(CONCEPT_KEY_PREFIX) :]
        cur_c = out.concepts.get(term_id) or ConceptState(term_id=term_id)
        d_state = _state_for(d.value)
        if d.is_override:
            out.concepts[term_id] = cur_c.model_copy(
                update={"state": d_state, "source": "declared"}
            )
            continue
        d_w = d.weight * _decay(
            store.sessions_since(db, profile.user_id, d.updated_at), DECLARED_HALF_LIFE_SESSIONS
        )
        if d_w >= DECLARED_CONCEPT_MIN_WEIGHT and _rank(d_state) > _rank(cur_c.state):
            out.concepts[term_id] = cur_c.model_copy(
                update={"state": d_state, "source": "declared"}
            )
    return out


# ── inputs ───────────────────────────────────────────────────────────────────


class _Stop:
    __slots__ = ("id", "lat", "lng", "water", "label", "catches")

    def __init__(self, id, lat, lng, water, label):
        self.id, self.lat, self.lng, self.water, self.label = id, lat, lng, water, label
        self.catches: list[dict] = []


class _Session:
    __slots__ = ("id", "date", "season", "stops")

    def __init__(self, id, date, season):
        self.id, self.date, self.season = id, date, season
        self.stops: list[_Stop] = []


def _sessions(db: Database, user_id: int) -> list[_Session]:
    names = set(db.table_names())
    if "stops" not in names or "sessions" not in names:
        return []
    links: dict[int, int] = {}
    if "pin_links" in names:
        links = dict(
            db.execute(
                "SELECT stop_id, pin_id FROM pin_links WHERE user_id = ?", [user_id]
            ).fetchall()
        )
    rows = db.execute(
        "SELECT s.id, s.date, s.date_approx, s.created_at, st.id, st.lat, st.lng, "
        "       st.ohn_segment_id, COALESCE(st.location_name, st.location_text) "
        "FROM sessions s JOIN stops st ON st.session_id = s.id "
        "WHERE s.user_id = ? AND st.user_id = ? ORDER BY s.id, st.id",
        [user_id, user_id],
    ).fetchall()
    by_id: dict[int, _Session] = {}
    stops: dict[int, _Stop] = {}
    for sid, date, approx, created, stop_id, lat, lng, seg, label in rows:
        sess = by_id.get(sid)
        if sess is None:
            when = date or approx or (created or "")[:10]
            sess = by_id[sid] = _Session(sid, when, _season(date, approx))
        stop = _Stop(stop_id, lat, lng, _water_key(stop_id, lat, lng, seg, links), label or "")
        sess.stops.append(stop)
        stops[stop_id] = stop

    if "catches" in names and stops:
        confirmed = ""
        if "species_confirmed" in {c.name for c in db["catches"].columns}:
            confirmed = " AND species_confirmed = 1"
        for stop_id, species, count, size_cm, size_txt in db.execute(
            "SELECT stop_id, species, count, biggest_size_cm, biggest_size FROM catches "
            f"WHERE user_id = ?{confirmed}",
            [user_id],
        ).fetchall():
            if stop_id in stops and species and species.strip().lower() != "unidentified sp.":
                stops[stop_id].catches.append(
                    {
                        "species": species.strip().lower(),
                        "count": count or 1,
                        "sized": size_cm is not None or bool((size_txt or "").strip()),
                    }
                )
    return sorted(by_id.values(), key=lambda s: (_sort_date(s.date), s.id))


def _water_key(stop_id, lat, lng, seg, links) -> str:
    if stop_id in links:
        return f"pin:{links[stop_id]}"
    if seg:
        return f"seg:{seg}"
    if lat is None or lng is None:
        return f"stop:{stop_id}"
    return f"grid:{round(lat / SAME_WATER_GRID_DEG)}_{round(lng / SAME_WATER_GRID_DEG)}"


def _season(date: str | None, approx: str | None) -> str | None:
    if date and re.match(r"\d{4}-\d{2}", date):
        return _SEASONS[int(date[5:7])]
    for word in re.findall(r"[a-z]+", (approx or "").lower()):
        if word in _MONTHS:
            return _SEASONS[_MONTHS[word]]
    return None


def _sort_date(when: str | None) -> str:
    if when and re.match(r"\d{4}-\d{2}", when):
        return when
    text = (when or "").lower()
    year = re.search(r"(\d{4})", text)
    words = re.findall(r"[a-z]+", text)
    month = next((_MONTHS[w] for w in words if w in _MONTHS), 1)
    day = 25 if "late" in words else 5 if "early" in words else 15
    return f"{year.group(1) if year else '0000'}-{month:02d}-{day:02d}"


# ── orientation ──────────────────────────────────────────────────────────────


def _orientation(db: Database, sessions: list[_Session]):
    decay = 0.5 ** (1 / SPECTRUM_HALF_LIFE_SESSIONS)
    dex: set[str] = set()
    caught_at_water: dict[str, set[str]] = defaultdict(set)
    seasons_by_species: dict[str, set[str]] = defaultdict(set)
    visited: set[str] = set()
    recorded_seen: set[str] = set()
    richness_cache: dict[str, LocalRichness] = {}

    evidence: list[SessionEvidence] = []
    num = den = 0.0
    for sess in sessions:
        num *= decay
        den *= decay
        ev = SessionEvidence(session_id=sess.id, date=sess.date)
        lifelist = specialist = 0.0
        new_this_session: set[str] = set()
        rich_conf: list[float] = []
        for stop in sess.stops:
            ev.places.append(stop.label)
            rich = None
            if stop.lat is not None and stop.lng is not None:
                rich = richness_cache.get(stop.water)
                if rich is None:
                    rich = richness_cache[stop.water] = local_richness(db, stop.lat, stop.lng)
            if rich is not None:
                ev.richness_basis.append(
                    f"{rich.basis} ({len(rich.species)} species, {rich.n_records} records)"
                )
                rich_conf.append(rich.confidence)
            recorded = set(rich.species) if rich else set()
            names = rich.common_names if rich else {}
            caught = {_canonical(c["species"], names) for c in stop.catches}

            if stop.water in visited:
                specialist += REPEAT_WATER_WEIGHT
                ev.reasons.append(f"returned to {stop.label or stop.water}")
                repeat = caught & caught_at_water[stop.water]
                if repeat:
                    specialist += REPEAT_WATER_SPECIES_WEIGHT
                    ev.reasons.append(f"caught {', '.join(sorted(repeat))} there again")
            elif rich is not None and rich.richness > 0:
                fresh = recorded - dex - recorded_seen
                gain = min(len(fresh) / rich.richness, 1.0)
                if gain > 0:
                    lifelist += NOVELTY_TRAVEL_WEIGHT * gain * rich.confidence
                    ev.reasons.append(
                        f"new water with {len(fresh)} recorded species new to the angler"
                    )

            for sp in caught - dex - new_this_session:
                new_this_session.add(sp)
                conf = (
                    rich.confidence
                    if rich and rich.basis != "none"
                    else UNRECORDED_PLACE_CONFIDENCE
                )
                if rich is None or rich.basis == "none":
                    w, why = LIFER_UNKNOWN_WEIGHT, "place unrecorded"
                elif sp in recorded:
                    w, why = LIFER_INTENDED_WEIGHT, "recorded there"
                elif rich.thin:
                    w, why = LIFER_UNKNOWN_WEIGHT, "thinly recorded there"
                else:
                    w, why = LIFER_ACCIDENTAL_WEIGHT, "not recorded there"
                lifelist += w * conf
                ev.reasons.append(f"new species {sp} ({why})")

            for sp in caught & dex:
                if sess.season and seasons_by_species[sp] - {sess.season}:
                    specialist += SEASON_SPAN_WEIGHT
                    ev.reasons.append(f"{sp} again in a different season")

            for sp in caught:
                caught_at_water[stop.water].add(sp)
                if sess.season:
                    seasons_by_species[sp].add(sess.season)
            visited.add(stop.water)
            recorded_seen |= recorded
        dex |= new_this_session

        total = lifelist + specialist
        ev.lifelist, ev.specialist = round(lifelist, 3), round(specialist, 3)
        if total > 0:
            ev.signal = round(lifelist / total, 3)
            ev.weight = round(min(total, SESSION_EVIDENCE_CAP), 3)
            num += ev.signal * ev.weight
            den += ev.weight
        evidence.append(ev)

    inferred = ProfileValue(
        value=_clip(num / den) if den else 0.5, source="inferred", weight=round(den, 4)
    )
    value = (0.5 * NEUTRAL_PRIOR_WEIGHT + num) / (NEUTRAL_PRIOR_WEIGHT + den)
    return evidence, BlendedValue(
        value=_clip(value), confidence=_confidence(den), inferred=inferred
    )


def _canonical(species: str, recorded_common: dict[str, str]) -> str:
    """A species key comparable with recorded scientific names."""
    sci = COMMON_TO_SCIENTIFIC.get(species) or recorded_common.get(species)
    return (sci or species).strip().lower()


# ── side values ──────────────────────────────────────────────────────────────


def _side_values(sessions: list[_Session]) -> tuple[BlendedValue, BlendedValue]:
    decay = 0.5 ** (1 / SIDE_HALF_LIFE_SESSIONS)
    totals = {"size": [0.0, 0.0], "micro": [0.0, 0.0]}  # [weighted hits, weight]
    for sess in sessions:
        for t in totals.values():
            t[0] *= decay
            t[1] *= decay
        for stop in sess.stops:
            for c in stop.catches:
                totals["size"][0] += 1.0 if c["sized"] else 0.0
                totals["size"][1] += 1.0
                is_micro = any(m in c["species"] for m in MICRO_MARKERS)
                totals["micro"][0] += 1.0 if is_micro else 0.0
                totals["micro"][1] += 1.0
    out = []
    for hits, weight in totals.values():
        inferred = ProfileValue(
            value=_clip(hits / weight) if weight else SIDE_PRIOR_VALUE,
            source="inferred",
            weight=round(weight, 4),
        )
        value = (SIDE_PRIOR_VALUE * SIDE_PRIOR_WEIGHT + hits) / (SIDE_PRIOR_WEIGHT + weight)
        out.append(
            BlendedValue(value=_clip(value), confidence=_confidence(weight), inferred=inferred)
        )
    return out[0], out[1]


# ── literacy ─────────────────────────────────────────────────────────────────


def _concepts(db: Database, user_id: int, sessions: list[_Session]) -> dict[str, ConceptState]:
    features = _features(db, user_id)
    logged: dict[str, dict] = defaultdict(lambda: {"count": 0, "sessions": set(), "refs": []})
    for sess in sessions:
        for stop in sess.stops:
            if stop.lat is None or stop.lng is None:
                continue
            for term_id, ref in _terms_near(features, stop.lat, stop.lng):
                e = logged[term_id]
                e["count"] += 1
                e["sessions"].add(sess.id)
                if len(e["refs"]) < 5:
                    e["refs"].append(f"stop:{stop.id}~{ref}")

    saved: dict[str, dict] = defaultdict(lambda: {"count": 0, "refs": []})
    if "pins" in db.table_names():
        for pin_id, lat, lng in db.execute(
            "SELECT id, lat, lng FROM pins WHERE user_id = ?", [user_id]
        ).fetchall():
            for term_id, ref in _terms_near(features, lat, lng):
                saved[term_id]["count"] += 1
                if len(saved[term_id]["refs"]) < 5:
                    saved[term_id]["refs"].append(f"pin:{pin_id}~{ref}")

    events = store.event_counts(db, user_id)
    out: dict[str, ConceptState] = {}
    for term_id in terms.TERM_ORDER:
        ev: list[ConceptEvidence] = []
        lg = logged.get(term_id)
        if lg:
            ev.append(
                ConceptEvidence(
                    kind="logged_near",
                    count=lg["count"],
                    sessions=len(lg["sessions"]),
                    refs=lg["refs"],
                )
            )
        sv = saved.get(term_id)
        if sv:
            ev.append(ConceptEvidence(kind="saved_near", count=sv["count"], refs=sv["refs"]))
        for kind in store.EVENT_KINDS:
            n, refs = events.get((term_id, kind), (0, []))
            if n:
                ev.append(ConceptEvidence(kind=kind, count=n, refs=refs))
        out[term_id] = ConceptState(term_id=term_id, state=_concept_state(ev), evidence=ev)
    return out


def _concept_state(evidence: list[ConceptEvidence]) -> str:
    by = {e.kind: e for e in evidence}
    logged_sessions = by["logged_near"].sessions if "logged_near" in by else 0
    hands_on = logged_sessions + (by["saved_near"].count if "saved_near" in by else 0)
    opened = by["definition_opened"].count if "definition_opened" in by else 0
    seen = by["seen_on_map"].count if "seen_on_map" in by else 0
    if (
        logged_sessions >= FAMILIAR_LOGGED_SESSIONS
        or (opened and hands_on)
        or seen >= FAMILIAR_SEEN_COUNT
    ):
        return "familiar"
    if hands_on or opened or seen >= INTRODUCED_SEEN_COUNT:
        return "introduced"
    return "unseen"


def _features(db: Database, user_id: int) -> list[tuple[str, float, float, str]]:
    """(term_id, lat, lng, ref) for every mapped feature that is an instance of a term.

    Public map features (spots) for everyone; weed edges only from this
    user's own reports, which are private.
    """
    names = set(db.table_names())
    out: list[tuple[str, float, float, str]] = []
    if "spots" in names:
        for spot_id, kind, lat, lng, attrs in db.execute(
            "SELECT spot_id, kind, lat, lng, structure_attrs_json FROM spots"
        ).fetchall():
            dam_kind = None
            if attrs:
                try:
                    dam_kind = json.loads(attrs).get("dam_kind")
                except (TypeError, ValueError, AttributeError):
                    dam_kind = None
            for term_id in terms.spot_terms(kind, dam_kind):
                out.append((term_id, lat, lng, spot_id))
    if "weed_reports" in names:
        for rid, a, b, c, d in db.execute(
            "SELECT id, bbox_min_lat, bbox_min_lng, bbox_max_lat, bbox_max_lng "
            "FROM weed_reports WHERE user_id = ?",
            [user_id],
        ).fetchall():
            out.append(("weed_edge", (a + c) / 2, (b + d) / 2, f"weed_report:{rid}"))
    return out


def _terms_near(features, lat: float, lng: float):
    seen: set[str] = set()
    for term_id, flat, flng, ref in features:
        if term_id in seen:
            continue
        if _metres(lat, lng, flat, flng) <= NEAR_FEATURE_M:
            seen.add(term_id)
            yield term_id, ref


# ── helpers ──────────────────────────────────────────────────────────────────


def _metres(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def _decay(sessions: int, half_life: float) -> float:
    return 0.5 ** (sessions / half_life)


def _confidence(weight: float) -> float:
    return round(1 - math.exp(-weight / CONFIDENCE_SCALE), 4)


def _clip(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), 4)


def _state_for(value: float) -> str:
    return "familiar" if value >= 0.75 else "introduced" if value >= 0.25 else "unseen"


def _rank(state: str) -> int:
    return {"unseen": 0, "introduced": 1, "familiar": 2}[state]
