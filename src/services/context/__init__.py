"""The central context layer.

All sources feed one shared layer. Claude reasons *over* retrieved specifics;
it is never the source of a claim about particular water.

Three entry points:

    describe(place)        everything known about one stretch of water
    explore(area, filters) ranks places the user hasn't been
    user_layer(user)       derived patterns, expertise, known gaps

Callers do not choose slices. Bundles are determined by caller type here, in
Python — letting surfaces request slices individually would recreate the
routing problem one layer down, which is the thing this layer exists to fix.
"""

import logging
from typing import Literal

from sqlite_utils import Database

from src.models.context import (
    ContextField,
    EmptyReason,
    ExploreResponse,
    ExploreResult,
    Place,
    PlaceContext,
)
from src.services.context import place as place_mod
from src.services.context import slices, translate

logger = logging.getLogger(__name__)

CallerType = Literal[
    "map_tap", "chat_place", "post_log", "coach", "trip_parse", "full"
]

# Which slices each caller gets. A map tap does not need live conditions —
# that saves both tokens and a live API call, and a tap should feel free.
_BUNDLES: dict[str, tuple[str, ...]] = {
    "map_tap": ("records", "water", "structure", "access"),
    # The conversational agent's place bundle: everything static, including the
    # user's own history, and nothing live. Conditions are a separate tool
    # because they are the only thing here that cannot be cached, and a bundle
    # that sometimes fires a live fetch is a bundle nobody can cache.
    "chat_place": ("records", "water", "structure", "access", "history"),
    "post_log": ("records", "conditions", "history"),
    "coach": ("records", "history", "water", "conditions"),
    "trip_parse": ("records", "history"),
    "full": ("records", "water", "structure", "access", "conditions", "history"),
}

# Which callers may spend a live web search when the corpus comes back empty.
# Escalation costs money and roughly two seconds, so it is opt-in by caller
# rather than by question: a map tap fires on every pan and must stay free,
# while someone who asked a question is owed the second rung before we tell
# them we have nothing.
_ESCALATING_CALLERS: frozenset[str] = frozenset({"coach", "full"})


def describe(
    db: Database,
    query: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    segment_id: int | None = None,
    radius_km: float = 5.0,
    caller: CallerType = "full",
    user_id: int = 1,
    species_filter: str | None = None,
    escalate: bool | None = None,
) -> PlaceContext | None:
    """Everything known about one stretch of water, bundled by caller type.

    Returns None only when the place itself cannot be resolved — which is a
    different failure from "resolved, but we know nothing about it", and the
    caller must be able to tell those apart.
    """
    resolved = place_mod.resolve(
        db,
        query=query,
        lat=lat,
        lng=lng,
        segment_id=segment_id,
        radius_km=radius_km,
        user_id=user_id,
    )
    if resolved is None:
        return None

    wanted = _BUNDLES.get(caller, _BUNDLES["full"])
    ctx = PlaceContext(place=resolved, bundle=caller)

    if "records" in wanted:
        may_escalate = caller in _ESCALATING_CALLERS if escalate is None else escalate
        ctx.records = slices.build_records(
            db,
            resolved,
            species_filter=species_filter,
            user_id=user_id,
            escalate=may_escalate,
        )
    if "water" in wanted:
        ctx.water = slices.build_water(db, resolved)
    if "structure" in wanted:
        ctx.structure = slices.build_structure(db, resolved)
    if "access" in wanted:
        ctx.access = slices.build_access(db, resolved)
    if "conditions" in wanted:
        ctx.conditions = slices.build_conditions(db, resolved)
    if "history" in wanted:
        ctx.history = slices.build_history(db, resolved, user_id=user_id)

    return ctx


def explore(
    db: Database,
    lat: float,
    lng: float,
    radius_km: float = 50.0,
    min_stream_order: int = 3,
    limit: int = 10,
    mode: str = "balanced",
    user_id: int = 1,
) -> ExploreResponse:
    """Rank places the user hasn't been.

    Wraps the untapped-potential scorer, which carries no habitat term: the
    SDM that supplied one scored 0.51-0.61 AUC and is gone from this path.
    What survives — observation pressure, access, structural bonus,
    remoteness — is independent of it and genuinely works.
    """
    from src.services.untapped_potential import load_cached_untapped, plausibility_gate

    df = load_cached_untapped()
    if df is None or df.empty:
        return ExploreResponse(empty_reason=EmptyReason.SOURCE_DOES_NOT_COVER_AREA)

    deg = radius_km / 111.0
    near = df[
        df["centroid_lat"].between(lat - deg, lat + deg)
        & df["centroid_lng"].between(lng - deg, lng + deg)
    ]
    if near.empty:
        return ExploreResponse(empty_reason=EmptyReason.NO_RECORDS_IN_RADIUS)

    if "stream_order" in near.columns:
        near = near[near["stream_order"] >= min_stream_order]

    # Report what the gate removed rather than dropping it silently.
    gate = plausibility_gate(near)
    excluded = near[~gate]
    near = near[gate]

    score_col = {
        "adventure": "untapped_score_adventure",
        "easy_access": "untapped_score_easy",
    }.get(mode, "untapped_score_balanced")
    if score_col not in near.columns:
        score_col = "untapped_score"

    near = near[near[score_col] > 0.0].sort_values(score_col, ascending=False)

    seen = _seen_segment_ids(db, user_id)
    if seen:
        near = near[~near["ogf_id"].isin(seen)]

    if near.empty:
        return ExploreResponse(
            empty_reason=EmptyReason.NO_RECORDS_IN_RADIUS,
            excluded_count=int(len(excluded)),
        )

    results = [
        ExploreResult(
            ogf_id=int(row["ogf_id"]),
            name=(str(row["watercourse_name"]) or None)
            if row.get("watercourse_name")
            else None,
            lat=float(row["centroid_lat"]),
            lng=float(row["centroid_lng"]),
            stream_order=int(row["stream_order"])
            if row.get("stream_order") == row.get("stream_order")
            else None,
            score=round(float(row[score_col]), 4),
            observation_pressure=round(float(row.get("observation_pressure", 0.0)), 3),
            access_score=round(float(row.get("access_score", 0.0)), 3),
            access_is_measured=_tristate(row.get("access_is_measured")),
            access=_access_field(
                round(float(row.get("access_score", 0.0)), 3),
                _tristate(row.get("access_is_measured")),
            ),
            is_confluence=bool(row.get("is_confluence_segment", False)),
        )
        for _, row in near.head(limit).iterrows()
    ]

    top_score = float(near[score_col].iloc[0])
    tied = int((near[score_col] == top_score).sum())

    return ExploreResponse(
        results=results,
        excluded_count=int(len(excluded)),
        excluded_examples=_gate_examples(excluded),
        tied_at_top=tied,
        results_on_placeholder_access=sum(
            1 for r in results if r.access_is_measured is False
        ),
        results_with_unknown_access_coverage=sum(
            1 for r in results if r.access_is_measured is None
        ),
    )


def _access_field(score: float, measured: bool | None) -> ContextField:
    """Wrap the access figure the same way every other value gets wrapped.

    The three states map onto empty reasons that already exist, which is the
    point of having them: a placeholder is a coverage gap in the world, and an
    unclassifiable score is a gap in our own pipeline. Those already have
    agreed wording, so this field does not need wording of its own.
    """
    if measured is True:
        return ContextField.recorded(
            score, source="OSM access points", meaning=translate.access_score(score)
        )
    if measured is False:
        return ContextField.empty(EmptyReason.SOURCE_DOES_NOT_COVER_AREA)
    return ContextField.empty(EmptyReason.FIELD_NOT_POPULATED_BY_SOURCE)


def _tristate(value) -> bool | None:
    """Pandas nullable booleans and a missing column both mean 'we don't know'.

    Coercing either to False would claim a segment sits outside the mapped
    access footprint on no evidence — inventing a fact about remoteness from a
    stale parquet.
    """
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def _gate_examples(excluded, limit: int = 3) -> list[str]:
    from src.services.untapped_potential import gate_exclusion_reason

    out: list[str] = []
    for _, row in excluded.head(limit).iterrows():
        reason = gate_exclusion_reason(row)
        if reason:
            out.append(reason)
    return out


def _seen_segment_ids(db: Database, user_id: int) -> set[int]:
    """Segments the user has already fished or explicitly dismissed."""
    seen: set[int] = set()
    if "stops" in db.table_names():
        rows = db.execute(
            "SELECT DISTINCT ohn_segment_id FROM stops "
            "WHERE user_id = ? AND ohn_segment_id IS NOT NULL",
            [user_id],
        ).fetchall()
        seen.update(int(r[0]) for r in rows if r[0] is not None)
    if "dismissed_segments" in db.table_names():
        rows = db.execute(
            "SELECT ogf_id FROM dismissed_segments WHERE user_id = ?", [user_id]
        ).fetchall()
        seen.update(int(r[0]) for r in rows if r[0] is not None)
    return seen


def describe_species(db: Database, name: str):
    """Species facts with provenance on every claim. Fails closed on SAR status."""
    from src.services.context.species import describe_species as _describe

    return _describe(db, name)


def user_layer(db: Database, user_id: int = 1, force: bool = False):
    """Derived patterns, demonstrated expertise, and known gaps.

    Served from the stored layer when the inputs have not changed. Recomputed
    when a session is logged, not when a question is asked — this used to
    re-read every stop the angler had ever logged, several times per
    conversation, to re-derive numbers whose inputs change a few times a
    season.

    `force` skips the read and recomputes. The write path uses it; nothing
    else should need to.
    """
    from src.services.context.user import build_user_layer
    from src.storage import user_patterns

    fingerprint = user_patterns.input_fingerprint(db, user_id)
    if not force:
        cached = user_patterns.load(db, user_id, fingerprint)
        if cached is not None:
            return cached

    layer = build_user_layer(db, user_id=user_id)
    user_patterns.store(db, layer, fingerprint)
    return layer


def recompute_user_layer(db: Database, user_id: int = 1):
    """Rebuild and store the derived layer. Call after writing a session.

    Separate from `user_layer(force=True)` only in intent: this is the name a
    write path should reach for, and it says at the call site that the write
    is what triggered the work.
    """
    return user_layer(db, user_id=user_id, force=True)


def species_history(db: Database, species: str, user_id: int = 1):
    """One angler's own record with one species — catches, blanks and insights."""
    from src.services.context.user import build_species_history

    return build_species_history(db, species, user_id=user_id)


__all__ = [
    "CallerType",
    "EmptyReason",
    "ExploreResponse",
    "ExploreResult",
    "Place",
    "PlaceContext",
    "describe",
    "describe_species",
    "explore",
    "recompute_user_layer",
    "species_history",
    "user_layer",
]
