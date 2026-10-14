"""Derived user layer.

Precomputed from logged activity. Never raw rows. What someone cares about
and which map concepts they know is derived from what they log
(`profile.py`); a declared value can seed it but never replaces behaviour.

The core epistemic rule lives here in code rather than in prompt prose:

  * General ecological knowledge applied to observed conditions is legitimate
    immediately, at n=1. That is not this module's business — it needs no
    comparison set, so nothing here gates it.
  * Claims about the user's *own* patterns need a comparison set. A pattern
    is only `is_claimable` with both arms present. Same facts, different
    grammar: principles now, personal tendencies when earned.
"""

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from sqlite_utils import Database

from src.models.context import (
    DerivedPattern,
    EmptyReason,
    Provenance,
    ProvenanceKind,
    RecordedInsight,
    SpeciesHistory,
    UserLayer,
)
from src.services.context.profile import derive_profile

logger = logging.getLogger(__name__)

# A species has to show up this often before we treat it as something the
# angler targets rather than something they happened to catch.
_TARGET_MIN_CATCHES = 3


def build_user_layer(db: Database, user_id: int = 1) -> UserLayer:
    """Recompute the derived layer for one user.

    Called when a session is logged, not per question — the coaching path
    this replaces loaded every stop ever logged and filtered in Python on
    every single request.
    """
    now = datetime.now(UTC).isoformat()
    profile = derive_profile(db, user_id)

    if "stops" not in db.table_names():
        return UserLayer(
            user_id=user_id, computed_at=now, known_gaps=["no trips logged yet"], profile=profile
        )

    rows = list(
        db.execute(
            "SELECT st.species_caught, st.was_productive, st.technique, st.gear, "
            "       st.water_clarity, st.water_level, st.location_name, s.date "
            "FROM stops st JOIN sessions s ON st.session_id = s.id "
            "WHERE st.user_id = ?",
            [user_id],
        ).fetchall()
    )
    if not rows:
        return UserLayer(
            user_id=user_id, computed_at=now, known_gaps=["no trips logged yet"], profile=profile
        )

    session_count = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id = ?", [user_id]
    ).fetchone()[0]

    species_counter: Counter[str] = Counter()
    blanks = 0
    clarity_outcomes: list[tuple[str, bool]] = []

    for species_json, productive, _technique, _gear, clarity, _level, _loc, _date in rows:
        caught = _species_list(species_json)
        species_counter.update(s.strip().lower() for s in caught if s.strip())
        if not productive:
            blanks += 1
        if clarity:
            clarity_outcomes.append((str(clarity).strip().lower(), bool(productive)))

    return UserLayer(
        user_id=user_id,
        total_sessions=int(session_count),
        total_stops=len(rows),
        blank_rate=round(blanks / len(rows), 3) if rows else None,
        species_logged=sorted(species_counter),
        target_species=_infer_targets(species_counter),
        profile=profile,
        patterns=_derive_patterns(clarity_outcomes),
        known_gaps=_known_gaps(rows),
        computed_at=now,
    )


def _species_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(s) for s in parsed] if isinstance(parsed, list) else []


def _infer_targets(counter: Counter[str]) -> list[str]:
    """What someone targets, read off what they repeatedly log.

    No hardcoded species list and no configured profile — if the system needs
    to know someone targets redhorse, it learns it from their logs.
    """
    return sorted(sp for sp, n in counter.items() if n >= _TARGET_MIN_CATCHES)


def _derive_patterns(clarity_outcomes: list[tuple[str, bool]]) -> list[DerivedPattern]:
    """Personal-tendency claims, each carrying its own comparison set.

    A pattern is emitted even when it is not yet claimable — the sample sizes
    travel with it, so the caller can say "one session is a hypothesis, not a
    rule" instead of silently over- or under-claiming.
    """
    if not clarity_outcomes:
        return []

    by_clarity: dict[str, list[bool]] = {}
    for clarity, productive in clarity_outcomes:
        by_clarity.setdefault(clarity, []).append(productive)

    # A comparison needs at least two conditions to compare.
    if len(by_clarity) < 2:
        only = next(iter(by_clarity))
        return [
            DerivedPattern(
                statement=f"only ever logged {only} water — no comparison available",
                sample_size=len(by_clarity[only]),
                comparison_size=0,
                confidence="low",
            )
        ]

    ranked = sorted(
        by_clarity.items(),
        key=lambda kv: (sum(kv[1]) / len(kv[1]), len(kv[1])),
        reverse=True,
    )
    best_clarity, best_outcomes = ranked[0]
    rest = sum(len(v) for _, v in ranked[1:])

    best_rate = sum(best_outcomes) / len(best_outcomes)
    other_rate = sum(sum(v) for _, v in ranked[1:]) / rest if rest else 0.0

    if best_rate <= other_rate:
        return []

    pattern = DerivedPattern(
        statement=f"more productive in {best_clarity} water",
        sample_size=len(best_outcomes),
        comparison_size=rest,
        confidence="low",
    )
    if pattern.is_claimable:
        pattern.confidence = "medium" if pattern.sample_size < 6 else "high"
    return [pattern]


def _known_gaps(rows: list) -> list[str]:
    """Which fields are missing too often to support a given kind of claim.

    This is what lets a surface say "I can't tell you that yet, and here is
    what would fix it" rather than quietly reasoning from absent data.
    """
    total = len(rows)
    if total == 0:
        return []

    # Column order matches the SELECT in build_user_layer.
    checks = {
        "technique": (2, "can't compare techniques"),
        "gear": (3, "can't compare rigs"),
        "water clarity": (4, "can't tell you how clarity affects your results"),
        "water level": (5, "can't tell you how flow affects your results"),
    }

    gaps = []
    for label, (idx, consequence) in checks.items():
        missing = sum(1 for r in rows if not r[idx])
        if missing / total > 0.5:
            gaps.append(f"{label} unrecorded on {missing}/{total} stops — {consequence}")
    return gaps


# -- one species, across every place -------------------------------------------

# How the recorded source of an insight maps onto provenance. `agent_synthesis`
# and `tactical_rules` are the assistant's own reasoning: legitimate, but they
# must not render as peers of something drawn from a survey or the user's log.
_INSIGHT_PROVENANCE: dict[str, ProvenanceKind] = {
    "agent_synthesis": ProvenanceKind.INFERENCE,
    "tactical_rules": ProvenanceKind.INFERENCE,
    "inat_pattern": ProvenanceKind.RECORD,
    "mnrf_survey": ProvenanceKind.RECORD,
    "reddit_pattern": ProvenanceKind.WEB,
    "trip_log": ProvenanceKind.RECORD,
    "user_correction": ProvenanceKind.RECORD,
}

_SPECIES_STOP_SQL = """
    SELECT st.location_name, st.location_text, st.species_caught,
           st.party_species_caught, st.was_productive, st.technique,
           st.gear, st.water_level, st.water_clarity, st.weather_notes,
           st.notes, s.date, s.date_approx
    FROM stops st JOIN sessions s ON st.session_id = s.id
    WHERE st.user_id = ?
"""

_INSIGHT_SQL = """
    SELECT conclusion, confidence, recommendation, source_type, source_detail,
           created_at
    FROM behavioral_insights
    WHERE LOWER(species) LIKE ? AND is_current = 1 AND user_id = ?
    ORDER BY confidence DESC, created_at DESC
"""


def build_species_history(
    db: Database, species: str, user_id: int = 1
) -> SpeciesHistory:
    """What this angler has actually done with one species.

    This used to live inline in the coaching service, which assembled its own
    prompt block from raw rows. Nothing bypasses the context layer, so it lives
    here — and the insights it returns now carry their source rather than
    arriving as anonymous sentences.
    """
    if "stops" not in db.table_names():
        return SpeciesHistory(
            species=species, empty_reason=EmptyReason.USER_NEVER_FISHED_HERE
        )

    rows = list(db.execute(_SPECIES_STOP_SQL, [user_id]).fetchall())
    target = species.lower()

    caught, blanks = [], 0
    for r in rows:
        (
            loc_name,
            loc_text,
            caught_json,
            party_json,
            productive,
            _tech,
            _gear,
            _level,
            _clarity,
            _weather,
            _notes,
            _date,
            _approx,
        ) = r
        mine = [s.lower() for s in _species_list(caught_json)]
        party = [s.lower() for s in _species_list(party_json)]
        if any(target in s for s in mine):
            caught.append(r)
        elif not productive and not any(target in s for s in party):
            blanks += 1

    history = SpeciesHistory(
        species=species,
        caught_stops=len(caught),
        blank_stops=blanks,
        locations=sorted({(r[0] or r[1] or "unknown") for r in caught}),
        productive_setups=[_setup_line(r) for r in caught],
        last_caught=max((r[11] for r in caught if r[11]), default=None),
        insights=_recorded_insights(db, target, user_id),
    )
    if not caught and blanks == 0 and not history.insights:
        history.empty_reason = EmptyReason.USER_NEVER_FISHED_HERE
    return history


def _setup_line(row) -> str:
    loc = row[0] or row[1] or "unknown"
    date = row[11] or row[12] or "undated"
    tech = row[5] or "technique unrecorded"
    gear = row[6] or "gear unrecorded"
    conditions = [
        c
        for c in (
            f"{row[7]} water" if row[7] else None,
            row[8],
            row[9],
        )
        if c
    ]
    cond = ", ".join(conditions) or "conditions unrecorded"
    return f"{loc} ({date}): {tech}, {gear}, {cond}"


def _recorded_insights(db: Database, target: str, user_id: int) -> list[RecordedInsight]:
    if "behavioral_insights" not in db.table_names():
        return []
    try:
        rows = list(db.execute(_INSIGHT_SQL, [f"%{target}%", user_id]).fetchall())
    except Exception:  # noqa: BLE001 - a missing column must not kill coaching
        logger.warning("behavioural insight lookup failed", exc_info=True)
        return []

    out = []
    for conclusion, confidence, recommendation, source_type, source_detail, created in rows:
        kind = _INSIGHT_PROVENANCE.get(source_type or "", ProvenanceKind.INFERENCE)
        out.append(
            RecordedInsight(
                conclusion=conclusion or "",
                confidence=confidence or "unverified",
                recommendation=recommendation,
                provenance=Provenance(
                    kind=kind,
                    source=source_detail or source_type or "stored insight",
                    date=str(created)[:10] if created else None,
                ),
            )
        )
    return out


def as_recorded_insight(insight) -> RecordedInsight:
    """Wrap a stored BehavioralInsight so its source travels with its text.

    Same provenance mapping the coaching path uses. Exposed because
    trip_enrichment rewrites insight conclusions, and rewriting a survey
    finding is not the same act as rewriting something the assistant
    concluded last week.
    """
    kind = _INSIGHT_PROVENANCE.get(
        getattr(insight, "source_type", "") or "", ProvenanceKind.INFERENCE
    )
    return RecordedInsight(
        conclusion=getattr(insight, "conclusion", "") or "",
        confidence=getattr(insight, "confidence", None) or "unverified",
        recommendation=getattr(insight, "recommendation", None),
        provenance=Provenance(
            kind=kind,
            source=getattr(insight, "source_detail", None)
            or getattr(insight, "source_type", None)
            or "stored insight",
        ),
    )
