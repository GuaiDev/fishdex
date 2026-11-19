"""Untapped potential scoring — combines pressure, access, and structure.

Formula per segment:
  untapped_score = (1 - observation_pressure) × access_modifier
                   × structural_bonus × remoteness_multiplier
                   × plausibility_gate

Where:
  observation_pressure = normalised observation_density_25km (0–1)
  access_modifier      = access_score (easy_access), (1-access+0.1) (adventure),
                         or 1.0 (balanced)
  structural_bonus     = confluence and waterbody proximity multiplier (1.0–2.0)
  remoteness_multiplier = 1.5 if obs_density==0, 1.25 if 1–4, 1.0 if 5+
  plausibility_gate    = 1.0 normally, 0.0 for segments with affirmative
                         evidence they are not fishable water (see below)

No habitat term. The SDM that formerly supplied one scored 0.51–0.61 AUC on
spatial cross-validation — barely better than random — and its unreliability
had been patched over with a 0.35 floor. A weak signal presented as a ranking
term is worse than no term, because it launders noise as confidence.

What replaces it is a gate, not a score. The gate only ever rules segments
OUT, and only on affirmative evidence (a mapped ditch, a measured hypoxic
reading). Missing data never excludes: 99.3% of segments have no thermal
class and 99.3% have no dissolved-oxygen reading, so treating absence as
disqualifying would erase the corpus. This mirrors the project rule that
water-quality data rules out implausible predictions but never confirms
presence.

Consequence, stated plainly: ranking is now driven by observation scarcity,
structure, and access. The gate trims known-bad water; it does not and cannot
rank water by quality. Judging whether a stretch is worth the drive is the
job of describe(), which surfaces the underlying records.

Result cached to data/processed/untapped_potential.parquet.
"""

import logging
from pathlib import Path

import pandas as pd

from src.services.accessibility import (
    compute_access_scores,
    load_cached_scores,
)

logger = logging.getLogger(__name__)

_PARQUET_PATH = Path("data/processed/untapped_potential.parquet")
_FEATURE_MATRIX_PATH = Path("data/processed/sdm_feature_matrix.parquet")
_KM_PER_DEGREE = 111.0


def compute_untapped_potential(
    db,
    feature_matrix: pd.DataFrame | None = None,
    force_recompute_access: bool = False,
    mode: str = "balanced",
) -> pd.DataFrame:
    """Compute untapped potential for all segments.

    mode options:
      "balanced"     — (1-pressure) × structure × remoteness  (default — access ignored)
      "easy_access"  — (1-pressure) × access × structure × remoteness  (road-accessible)
      "adventure"    — (1-pressure) × (1-access+0.1) × structure × remoteness  (remote)

    All modes are multiplied by the plausibility gate. There is no species
    parameter: without a habitat model there is nothing species-specific to
    weight by, and accepting one would imply a per-species ranking that the
    data cannot support.

    All three scores are always computed and stored as separate columns
    (untapped_score_balanced, untapped_score_easy, untapped_score_adventure).
    The `mode` parameter determines which becomes the primary `untapped_score`
    used for sorting and agent-facing queries.

    Caches result to data/processed/untapped_potential.parquet.
    """
    if feature_matrix is None:
        if not _FEATURE_MATRIX_PATH.exists():
            raise FileNotFoundError("Feature matrix not found. Run `make build-features` first.")
        feature_matrix = pd.read_parquet(_FEATURE_MATRIX_PATH)

    # Exclude Virtual Flow segments — OHN connectivity segments through lakes,
    # not fishable stream reaches.
    if "watercourse_type" in feature_matrix.columns:
        feature_matrix = feature_matrix[
            feature_matrix["watercourse_type"] != "Virtual Flow"
        ].copy()
    elif "stream_segments" in db.table_names():
        vf_ids = {
            r["ogf_id"]
            for r in db["stream_segments"].rows_where("watercourse_type = 'Virtual Flow'")
        }
        feature_matrix = feature_matrix[~feature_matrix["ogf_id"].isin(vf_ids)].copy()

    # --- access scores ---
    access_scores = None
    if not force_recompute_access:
        access_scores = load_cached_scores()

    if access_scores is None:
        logger.info("Computing access scores (not cached)...")
        access_scores = compute_access_scores(db, feature_matrix)

    # --- observation pressure ---
    pressure = _compute_pressure(feature_matrix)

    # --- merge onto feature matrix ---
    base = feature_matrix[["ogf_id", "centroid_lat", "centroid_lng", "stream_order"]].copy()

    if "watercourse_name" in feature_matrix.columns:
        base["watercourse_name"] = feature_matrix["watercourse_name"].fillna("")
    else:
        base["watercourse_name"] = ""

    if "watercourse_type" in feature_matrix.columns:
        base["watercourse_type"] = feature_matrix["watercourse_type"].fillna("")
    else:
        base["watercourse_type"] = ""

    if "observation_density_25km" in feature_matrix.columns:
        base["observation_density_25km"] = (
            feature_matrix["observation_density_25km"].fillna(0).astype(int)
        )
    else:
        base["observation_density_25km"] = 0

    # Phase 3a structural features — pass through from feature matrix if present
    _struct_cols = [
        "is_confluence_segment",
        "distance_to_nearest_confluence_km",
        "nearest_waterbody_distance_m",
        "connected_to_waterbody",
    ]
    for col in _struct_cols:
        if col in feature_matrix.columns:
            base[col] = feature_matrix[col].values
        elif col in ("is_confluence_segment", "connected_to_waterbody"):
            base[col] = False
        else:
            base[col] = float("nan")

    if "substrate_category" in feature_matrix.columns:
        base["substrate_category"] = feature_matrix["substrate_category"].fillna("").values
    else:
        base["substrate_category"] = ""

    # Dissolved oxygen — read by the plausibility gate. Measured on ~0.7% of
    # segments; NaN elsewhere, which the gate treats as "no evidence", not
    # "unfishable".
    if "do_median_mgl" in feature_matrix.columns:
        base["do_median_mgl"] = feature_matrix["do_median_mgl"].values
    else:
        base["do_median_mgl"] = float("nan")

    base = base.set_index("ogf_id")

    # A placeholder access score is not a low access score, and until now the
    # ranking could not tell the difference: segments outside the ~55 km OSM
    # footprint normalised to ~0.27 and were ranked on as though measured.
    # Same failure as claiming fish move freely past a location whose barrier
    # count was never ingested — a default presented as a reading — except here
    # it silently reorders results instead of stating a false fact.
    from src.services.accessibility import load_cached_coverage

    measured = load_cached_coverage()
    if measured is not None:
        base["access_is_measured"] = measured.reindex(base.index).astype("boolean")
    else:
        # Cache predates the column. Unknown — which is not the same as
        # "outside the footprint", and saying so would invent a fact about
        # remoteness out of a stale parquet. `make compute-access` fixes it.
        base["access_is_measured"] = pd.array([pd.NA] * len(base), dtype="boolean")
        logger.warning(
            "Access scores carry no coverage column — every access figure below "
            "is unclassifiable as reading or placeholder. Run `make compute-access`."
        )

    base["access_score"] = access_scores.reindex(base.index).fillna(0.5)
    base["observation_pressure"] = pressure.reindex(base.index).fillna(0.0)

    _unmeasured = int((base["access_is_measured"] == False).sum())  # noqa: E712
    if _unmeasured:
        logger.info(
            "Access placeholder on %d of %d segments (%.1f%%) — these lie "
            "outside the ingest footprint, and the easy_access/adventure modes "
            "rank on a default for them",
            _unmeasured,
            len(base),
            100.0 * _unmeasured / len(base),
        )

    # Plausibility gate — removes water we have affirmative evidence is not
    # fishable. Never ranks; only zeroes. See module docstring.
    _gate = plausibility_gate(base).astype(float)
    base["passes_plausibility_gate"] = _gate.astype(bool)
    logger.info(
        "Plausibility gate excluded %d of %d segments (%.2f%%)",
        int((_gate == 0).sum()),
        len(base),
        100.0 * float((_gate == 0).mean()),
    )

    # Compute all three mode scores so the map can toggle between them
    _p = base["observation_pressure"]
    _a = base["access_score"]
    _struct = _structural_bonus(base)
    _remote = _remoteness_multiplier(base["observation_density_25km"])

    logger.info("Balanced:  (1-pressure) × structural × remoteness × gate  [no access]")
    logger.info("Easy:      (1-pressure) × access × structural × remoteness × gate")
    logger.info("Adventure: (1-pressure) × (1-access+0.1) × structural × remoteness × gate")

    base["untapped_score_balanced"] = (1.0 - _p) * _struct * _remote * _gate
    base["untapped_score_easy"] = (1.0 - _p) * _a * _struct * _remote * _gate
    base["untapped_score_adventure"] = (1.0 - _p) * (1.0 - _a + 0.1) * _struct * _remote * _gate

    if mode == "adventure":
        base["untapped_score"] = base["untapped_score_adventure"]
    elif mode == "easy_access":
        base["untapped_score"] = base["untapped_score_easy"]
    else:  # balanced (default)
        base["untapped_score"] = base["untapped_score_balanced"]

    result = base.reset_index().sort_values("untapped_score", ascending=False)

    # Pressure sanity check — log Oakville-area stats (lat 43.3-43.6, lng -80.0 to -79.4)
    oak_mask = (
        result["centroid_lat"].between(43.3, 43.6)
        & result["centroid_lng"].between(-80.0, -79.4)
    )
    if oak_mask.any():
        oak = result[oak_mask]
        logger.info(
            "Oakville area (%d segs): pressure mean=%.3f  balanced_score mean=%.4f",
            int(oak_mask.sum()),
            float(oak["observation_pressure"].mean()),
            float(oak["untapped_score_balanced"].mean()),
        )

    _PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(_PARQUET_PATH, index=False)
    logger.info("Untapped potential written to %s", _PARQUET_PATH)

    return result


# `explore()` and the card builder both read this ~8 MB parquet on every call,
# and a single spot build reads it several times. Keep the parsed frame in
# memory until the file changes. The key is the file's mtime (so a rebuild is
# picked up without a restart) plus its path (so repointing _PARQUET_PATH never
# serves another file's frame).
_cached_untapped: pd.DataFrame | None = None
_cached_untapped_key: tuple[Path, int] | None = None


def load_cached_untapped() -> pd.DataFrame | None:
    """Load cached untapped potential parquet, or None if not computed.

    The parsed parquet is reused in-process until the file's modification time
    changes, so repeated calls read the 8 MB file only once. A rebuild writes a
    new mtime and is picked up on the next call without a restart.
    """
    global _cached_untapped, _cached_untapped_key
    if not _PARQUET_PATH.exists():
        _cached_untapped = None
        _cached_untapped_key = None
        return None
    key = (_PARQUET_PATH, _PARQUET_PATH.stat().st_mtime_ns)
    if _cached_untapped is not None and _cached_untapped_key == key:
        return _cached_untapped
    _cached_untapped = pd.read_parquet(_PARQUET_PATH)
    _cached_untapped_key = key
    return _cached_untapped


# ── enrichment helpers ────────────────────────────────────────────────────────


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2  # noqa: E501
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _access_note(is_crown_land: bool, access_score: float) -> str:
    """Generate a human-readable access note based on crown land status and access score."""
    if is_crown_land:
        return (
            "Crown land — public access generally permitted for fishing. "
            "Verify no specific restrictions."
        )
    if access_score < 0.3:
        return (
            "⚠️ Access not verified — segment may cross private land. "
            "Check Ontario Crown Land map at geohub.lio.gov.on.ca before visiting. "
            "Low road access + private land = trespassing risk."
        )
    return "Road or park access nearby — verify public right of way."


# Dissolved oxygen below this cannot support a persistent fish community.
# Applied only where a reading actually exists.
_DO_FLOOR_MGL = 4.0

# OHN watercourse types that are not fishable stream reaches. "Virtual Flow"
# and "Virtual Connector" are connectivity artifacts through lakes, not water
# you can stand in; "Ditch" is mapped drainage infrastructure.
_NON_FISHABLE_TYPES = {"Virtual Flow", "Virtual Connector", "Ditch"}


def plausibility_gate(df: pd.DataFrame) -> pd.Series:
    """Boolean mask: True = no evidence this isn't fishable water.

    Rules segments OUT on affirmative evidence only. A segment with no data
    passes — absence of evidence is not evidence of absence, and for the
    fields involved absence is the overwhelming majority case.

    This is deliberately not a quality score. It cannot rank two segments
    that both pass; it only removes ones we can show are not viable.
    """
    keep = pd.Series(True, index=df.index)

    if "watercourse_type" in df.columns:
        keep &= ~df["watercourse_type"].astype(str).isin(_NON_FISHABLE_TYPES)

    # Measured hypoxia. NaN comparisons are False, so unmeasured segments
    # are never excluded here.
    if "do_median_mgl" in df.columns:
        keep &= ~(df["do_median_mgl"] < _DO_FLOOR_MGL)

    return keep


def gate_exclusion_reason(row) -> str | None:
    """Why a single segment failed the gate, or None if it passed.

    Callers surface this instead of dropping segments silently — an excluded
    segment with a stated reason is information; a missing row is not.
    """
    wtype = str(row.get("watercourse_type") or "")
    if wtype in _NON_FISHABLE_TYPES:
        if wtype == "Ditch":
            return "mapped as a drainage ditch, not a stream reach"
        return f"OHN {wtype.lower()} — a connectivity artifact, not a fishable reach"

    do = row.get("do_median_mgl")
    if do is not None and not pd.isna(do) and float(do) < _DO_FLOOR_MGL:
        return (
            f"measured dissolved oxygen {float(do):.1f} mg/L is below the "
            f"{_DO_FLOOR_MGL} mg/L floor for a persistent fish community"
        )

    return None


def _remoteness_multiplier(observation_density: pd.Series) -> pd.Series:
    """Bonus for genuinely unexplored water based on observation density in 25km radius.

    0 observations  → 1.5× (zero crowdsourced records = genuinely unexplored)
    1–4 observations → 1.25× (sparse = likely undersampled, not fishless)
    5+ observations  → 1.0× (sufficient sampling, no bonus)
    """
    mult = pd.Series(1.0, index=observation_density.index, dtype=float)
    mult[observation_density == 0] = 1.5
    mult[(observation_density > 0) & (observation_density < 5)] = 1.25
    return mult


def _structural_bonus(df: pd.DataFrame) -> pd.Series:
    """Multiplicative bonus for structural fish congregation features. Capped at 2.0."""
    import numpy as np

    bonus = pd.Series(1.0, index=df.index, dtype=float)

    if "is_confluence_segment" in df.columns:
        is_conf = df["is_confluence_segment"].fillna(False).astype(bool)
        bonus += np.where(is_conf, 0.4, 0.0)

        if "distance_to_nearest_confluence_km" in df.columns:
            dist = df["distance_to_nearest_confluence_km"].fillna(float("inf"))
            near_conf = (~is_conf) & (dist < 0.5)
            bonus += np.where(near_conf, 0.2, 0.0)

    if "connected_to_waterbody" in df.columns:
        is_wb = df["connected_to_waterbody"].fillna(False).astype(bool)
        bonus += np.where(is_wb, 0.3, 0.0)

    return bonus.clip(upper=2.0)


def _compute_mode_score(df: pd.DataFrame, mode: str) -> pd.Series:
    """Return untapped scores for each row based on mode, structural bonus, and remoteness."""
    p = df["observation_pressure"]
    a = df["access_score"]
    if mode == "adventure":
        base = (1.0 - p) * (1.0 - a + 0.1)
    elif mode == "balanced":
        base = 1.0 - p
    else:  # easy_access
        base = (1.0 - p) * a

    # Default density to 5 (no remoteness bonus) when column is absent
    density = (
        df["observation_density_25km"]
        if "observation_density_25km" in df.columns
        else pd.Series(5, index=df.index, dtype=float)
    )
    gate = plausibility_gate(df).astype(float)
    return base * _structural_bonus(df) * _remoteness_multiplier(density) * gate



_SATURATED_DENSITY = 10_000.0
"""Reference density treated as fully sampled.

Normalising against the dataset maximum (max ≈ 1837) crushed Oakville-area
segments (density ≈ 1289, 99th percentile) to pressure ≈ 0.95, making their
untapped scores approach zero. Using a fixed reference above the dataset max
maps Oakville to pressure ≈ 0.78, rural areas to ≈ 0.50, and wilderness to
≈ 0.10 (floor) — meaningful differentiation at every density level.
"""


def _compute_pressure(feature_matrix: pd.DataFrame) -> pd.Series:
    """Normalise observation_density_25km to [0.10, 1.0] using a fixed log reference.

    pressure = log1p(density) / log1p(SATURATED_DENSITY)
    clipped to [0.10, 1.0].

    Oakville (density ≈ 1289) → 0.78 pressure → (1-p) = 0.22 (was ≈ 0.05).
    Rural stream (density ≈ 100) → 0.50 pressure → (1-p) = 0.50.
    Wilderness (density = 0) → 0.10 pressure floor → (1-p) = 0.90.

    Floor at 0.10 prevents the remoteness bonus from over-inflating data-void
    segments relative to well-surveyed rural streams.
    """
    import numpy as np

    col = feature_matrix.set_index("ogf_id")["observation_density_25km"].fillna(0.0)
    pressure = np.log1p(col) / np.log1p(_SATURATED_DENSITY)
    return pressure.clip(lower=0.10, upper=1.0).rename("observation_pressure")


# ── seen-before helpers ───────────────────────────────────────────────────────

_SNAP_RADIUS_KM = 0.5  # 500m — trip location must be this close to snap to a segment


def _snap_trips_to_segments(db) -> list[int]:
    """Snap trip/stop lat/lng locations to the nearest OHN segment centroid within 500m."""
    raw_trips: list[dict] = []
    for table in ("trips", "stops", "parsed_trips"):
        if table in db.table_names():
            raw_trips.extend(db[table].rows_where("lat IS NOT NULL AND lng IS NOT NULL"))
    trips = raw_trips
    if not trips:
        return []
    if not _FEATURE_MATRIX_PATH.exists():
        return []

    import numpy as np
    from scipy.spatial import cKDTree

    try:
        fm = pd.read_parquet(
            _FEATURE_MATRIX_PATH,
            columns=["ogf_id", "centroid_lat", "centroid_lng"],
        )
        fm = fm.dropna(subset=["centroid_lat", "centroid_lng"])
    except Exception:
        return []

    if fm.empty:
        return []

    ogf_ids = fm["ogf_id"].tolist()
    coords = np.array(
        [
            [lat * 111.0, lng * 80.5]
            for lat, lng in zip(fm["centroid_lat"], fm["centroid_lng"])
        ]
    )
    tree = cKDTree(coords)

    seen = []
    for trip in trips:
        q = np.array([[float(trip["lat"]) * 111.0, float(trip["lng"]) * 80.5]])
        dist_km, idx = tree.query(q)
        if dist_km[0] <= _SNAP_RADIUS_KM:
            seen.append(ogf_ids[idx[0]])
    return seen
