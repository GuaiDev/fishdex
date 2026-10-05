"""Tests for the SDM training pipeline (Phase 2c).

All tests use synthetic data — no live DB required.
No model accuracy assertions — outputs are data-dependent.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.services.sdm_training import (
    _ALL_FEATURES,
    generate_pseudo_absences,
    predict_all_segments,
    prepare_species_data,
    train_species_model,
)
from src.storage.database import get_db

# ── synthetic helpers ─────────────────────────────────────────────────────────

_N = 80  # number of synthetic segments


def _make_features(n: int = _N, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Segments are laid out on a snaking 2D grid, not a straight diagonal.
    # A diagonal made both coordinates increase together, so quadrant-based
    # spatial block CV only ever populated two of four quadrants and every
    # fold was skipped — the smoke tests were passing on the old 0.5 sentinel
    # rather than on a scored model. The snake keeps consecutive indices
    # spatially adjacent, which the _coords_at-based tests rely on, while
    # filling all four quadrants.
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    idx = np.arange(n)
    row_of = idx // cols
    col_in_row = idx % cols
    # reverse every other row so index n and n+1 stay neighbours
    col_of = np.where(row_of % 2 == 0, col_in_row, cols - 1 - col_in_row)
    lats = 43.2 + (row_of / max(rows - 1, 1)) * 1.6
    lngs = -80.5 + (col_of / max(cols - 1, 1)) * 2.0

    summer_temp = np.full(n, np.nan)
    summer_temp[:10] = rng.uniform(10, 22, 10)

    df = pd.DataFrame(
        {
            "ogf_id": list(range(1, n + 1)),
            "centroid_lat": lats,
            "centroid_lng": lngs,
            "stream_order": rng.integers(1, 5, n),
            "length_m": rng.uniform(300, 8000, n),
            "flow_verified": rng.integers(0, 2, n).astype(bool),
            "substrate_category": rng.choice(["coarse", "fine", "bedrock", "organic"], n),
            "thermal_regime": np.where(np.arange(n) < 10, "coldwater", "unknown"),
            "summer_mean_temp_c": summer_temp,
            "do_median_mgl": np.where(np.arange(n) < 10, rng.uniform(6, 10, n), np.nan),
            "ph_median": np.where(np.arange(n) < 10, rng.uniform(6.5, 8.0, n), np.nan),
            "conductivity_median_us_cm": np.where(
                np.arange(n) < 10, rng.uniform(80, 200, n), np.nan
            ),
            "ept_quality": np.where(np.arange(n) < 10, "high", "unknown"),
            "ept_proportion": np.where(np.arange(n) < 10, rng.uniform(0.3, 0.8, n), np.nan),
            "barrier_count_upstream": rng.integers(0, 5, n),
            "distance_to_nearest_observation_km": rng.uniform(0.5, 30, n),
            "observation_density_25km": rng.integers(0, 20, n),
            "is_stocked_within_5yr": np.zeros(n, dtype=bool),
            "pwqmn_coverage": np.zeros(n, dtype=bool),
            # Phase 3a structural features
            "is_confluence_segment": np.zeros(n, dtype=bool),
            "distance_to_nearest_confluence_km": rng.uniform(0.1, 5.0, n),
            "nearest_waterbody_distance_m": np.where(
                np.arange(n) < 5, rng.uniform(50, 450, n), np.nan
            ),
            "connected_to_waterbody": np.where(np.arange(n) < 5, True, False),
        }
    )
    return df


_obs_counter = 0


def _add_obs(db, species: str, coords: list[tuple[float, float]]) -> None:
    global _obs_counter
    for lat, lng in coords:
        _obs_counter += 1
        db["observations"].insert(
            {
                "observation_id": _obs_counter,
                "species": species,
                "common_name": species,
                "taxon_id": 9999,
                "lat": lat,
                "lng": lng,
                "observed_on": "2024-06-01",
                "quality_grade": "research",
                "photo_url": None,
                "observer": "tester",
                "place_guess": "test",
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-05-01T00:00:00",
                "geoprivacy": "open",
                "is_obscured": 0,
                "obscuration_radius_km": None,
            },
            replace=True,
        )


def _add_gbif(db, species: str, coords: list[tuple[float, float]]) -> None:
    global _obs_counter
    for lat, lng in coords:
        _obs_counter += 1
        db["gbif_observations"].insert(
            {
                "gbif_key": _obs_counter + 500_000,
                "species": species,
                "common_name": species,
                "taxon_key": 8888,
                "lat": lat,
                "lng": lng,
                "observed_on": "2024-06-01",
                "country_code": "CA",
                "dataset_name": "test",
                "basis_of_record": "HUMAN_OBSERVATION",
                "coordinate_uncertainty_m": 100.0,
                "jurisdiction": "CA-ON",
                "ingested_at": "2026-05-01T00:00:00",
            },
            replace=True,
        )


def _add_trip_log_stop(
    db,
    common_names: list[str],
    lat: float | None,
    lng: float | None,
    was_productive: int = 1,
) -> None:
    """Log one stop the way the trip log does — a session row and a stop on it."""
    if not db["sessions"].exists() or not db["sessions"].count:
        db["sessions"].insert({"id": 1, "date": "2026-05-01"}, replace=True)
    db["stops"].insert(
        {
            "session_id": 1,
            "location_text": "test water",
            "location_name": "Test Water",
            "lat": lat,
            "lng": lng,
            "species_caught": json.dumps(common_names),
            "party_species_caught": json.dumps([]),
            "was_productive": was_productive,
        }
    )


def _coords_at(df: pd.DataFrame, indices: list[int]) -> list[tuple[float, float]]:
    return [(df.iloc[i]["centroid_lat"], df.iloc[i]["centroid_lng"]) for i in indices]


# ── prepare_species_data ──────────────────────────────────────────────────────


def test_prepare_species_data_loads_presence_records(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    _add_obs(db, "Semotilus atromaculatus", _coords_at(df, range(5, 15)))

    X, y = prepare_species_data("Semotilus atromaculatus", db, df)

    assert len(X) > 0
    assert (y == 1.0).all()
    assert set(X.columns) == set(_ALL_FEATURES)
    assert X.index.name == "ogf_id"


def test_prepare_species_data_combines_inat_and_gbif(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    _add_obs(db, "Perca flavescens", _coords_at(df, range(5, 10)))  # 5 iNat
    _add_gbif(db, "Perca flavescens", _coords_at(df, range(20, 25)))  # 5 GBIF

    X, y = prepare_species_data("Perca flavescens", db, df)

    # Up to 10 unique segments (some might snap to same segment)
    assert len(X) >= 5


def test_prepare_species_data_includes_trip_log_only_species(tmp_path: Path):
    """Trip-log catches alone produce presence rows — the query's output survives.

    tests/test_trip_log_sdm.py proves the trip-log query selects the right
    stops; this proves those stops reach X. With no iNat or GBIF record for
    the species, a trip-log stop is the only thing that can put a row in the
    result, so an empty X means the handoff is broken rather than the query.
    """
    df = _make_features()
    db = get_db(tmp_path / "test.db")

    caught_at = [30, 31, 32]
    for lat, lng in _coords_at(df, caught_at):
        _add_trip_log_stop(db, ["creek chub"], lat, lng)
    # An unproductive stop elsewhere: the filtering has to survive the handoff
    # too, not just the presences.
    unproductive_lat, unproductive_lng = _coords_at(df, [50])[0]
    _add_trip_log_stop(db, ["creek chub"], unproductive_lat, unproductive_lng, was_productive=0)

    # Nothing in the occurrence tables — trip log is the only possible source.
    for table in ("observations", "gbif_observations"):
        n = db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE LOWER(species) = ?",
            ["semotilus atromaculatus"],
        ).fetchone()[0]
        assert n == 0

    X, y = prepare_species_data("Semotilus atromaculatus", db, df)

    assert set(X.index) == set(df.iloc[caught_at]["ogf_id"])
    assert df.iloc[50]["ogf_id"] not in set(X.index)
    assert len(y) == len(X)
    assert (y == 1.0).all()
    assert set(X.columns) == set(_ALL_FEATURES)
    assert X.index.name == "ogf_id"


def test_prepare_species_data_stocking_exclusion(tmp_path: Path):
    df = _make_features().copy()
    df.loc[df["ogf_id"] <= 15, "is_stocked_within_5yr"] = True
    db = get_db(tmp_path / "test.db")

    # Observations on stocked segments + clean segments
    stocked_coords = _coords_at(df, range(0, 8))
    clean_coords = _coords_at(df, range(30, 38))
    _add_obs(db, "Oncorhynchus mykiss", stocked_coords + clean_coords)

    X_with, _ = prepare_species_data("Oncorhynchus mykiss", db, df, stocking_exclusion=True)
    X_without, _ = prepare_species_data("Oncorhynchus mykiss", db, df, stocking_exclusion=False)

    # Exclusion should produce fewer presences
    assert len(X_with) < len(X_without)
    # No stocked segments in result
    assert not any(df.loc[df["ogf_id"].isin(X_with.index), "is_stocked_within_5yr"])


def test_prepare_species_data_no_records_returns_empty(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")

    X, y = prepare_species_data("Ghost fish", db, df)

    assert len(X) == 0
    assert len(y) == 0


def test_prepare_species_data_bass_pooling(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    # Add records for both Micropterus species
    _add_obs(db, "Micropterus nigricans", _coords_at(df, range(5, 10)))
    _add_obs(db, "Micropterus salmoides", _coords_at(df, range(20, 25)))

    X_nigricans, _ = prepare_species_data("Micropterus nigricans", db, df)
    X_salmoides, _ = prepare_species_data("Micropterus salmoides", db, df)

    # Both names trigger pooled lookup — same result
    assert len(X_nigricans) == len(X_salmoides)


# ── generate_pseudo_absences ──────────────────────────────────────────────────


def test_generate_pseudo_absences_ratio(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    # Background observations on many segments
    _add_obs(db, "Other fish", _coords_at(df, range(0, _N)))

    presence_ids = list(df["ogf_id"].iloc[:5])
    absences = generate_pseudo_absences(presence_ids, df, db, ratio=5)

    # Should generate up to 5× presence count
    assert len(absences) <= len(presence_ids) * 5
    assert len(absences) > 0


def test_generate_pseudo_absences_no_target_species_overlap(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    _add_obs(db, "Background fish", _coords_at(df, range(0, 40)))

    presence_ids = [1, 2, 3, 4, 5]
    absences = generate_pseudo_absences(presence_ids, df, db)

    # Pseudo-absences must not include confirmed-presence segments
    assert not (set(absences) & set(presence_ids))


def test_generate_pseudo_absences_min_distance_buffer(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    # Background observations across all segments
    _add_obs(db, "Background", _coords_at(df, range(0, _N)))

    presence_ids = [df["ogf_id"].iloc[40]]  # single presence in the middle
    # 10km buffer ≈ 0.09° — adjacent segments should be excluded
    absences = generate_pseudo_absences(presence_ids, df, db, min_network_distance_km=10.0)

    # No absence should be suspiciously close to presence
    pres_lat = df.loc[df["ogf_id"] == presence_ids[0], "centroid_lat"].iloc[0]
    pres_lng = df.loc[df["ogf_id"] == presence_ids[0], "centroid_lng"].iloc[0]
    for abs_id in absences:
        row = df.loc[df["ogf_id"] == abs_id].iloc[0]
        dlat = row["centroid_lat"] - pres_lat
        dlng = row["centroid_lng"] - pres_lng
        dist = (dlat**2 + dlng**2) ** 0.5
        assert dist > 0.09, f"Absence {abs_id} too close to presence"


def test_generate_pseudo_absences_no_background_returns_empty(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    # No background observations at all
    presence_ids = [1, 2, 3]
    absences = generate_pseudo_absences(presence_ids, df, db)
    assert absences == []


# ── train_species_model (smoke tests) ────────────────────────────────────────


def _setup_smoke_db(tmp_path: Path, df: pd.DataFrame, species: str) -> object:
    """Create a test DB with 20 presence records and a background population."""
    db = get_db(tmp_path / "test.db")
    # 20 presence records for target species
    _add_obs(db, species, _coords_at(df, range(10, 30)))
    # Background observations (many other species + locations)
    for bg_sp in ["Background A", "Background B", "Background C"]:
        _add_obs(db, bg_sp, _coords_at(df, range(0, _N)))
    return db


def test_train_species_model_smoke(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Semotilus atromaculatus")

    result = train_species_model("Semotilus atromaculatus", db, df)

    assert result["species"] == "Semotilus atromaculatus"
    assert result["n_presence"] >= 5
    assert result["n_pseudo_absence"] > 0
    # None is a legitimate outcome (no usable spatial fold) and must not be
    # confused with a score; whichever it is, the note has to agree.
    auc = result["spatial_cv_auc"]
    assert auc is None or isinstance(auc, float)
    if auc is not None:
        assert 0.0 <= auc <= 1.0
    assert (auc is None) == ("not evaluable" in result["spatial_cv_note"])
    assert "model" in result


def test_train_species_model_auc_returned(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Perca flavescens")

    result = train_species_model("Perca flavescens", db, df)

    assert "spatial_cv_auc" in result
    auc = result["spatial_cv_auc"]
    assert auc is None or isinstance(auc, float)
    assert result["spatial_cv_folds_used"] >= 0


def test_train_species_model_feature_importances_sum_to_one(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Lepomis gibbosus")

    result = train_species_model("Lepomis gibbosus", db, df)

    imps = result["feature_importances"]
    assert isinstance(imps, dict)
    # All original feature names should be present
    assert set(imps.keys()) == set(_ALL_FEATURES)
    total = sum(imps.values())
    assert abs(total - 1.0) < 1e-5, f"Importances sum to {total}, expected ~1.0"


def test_train_species_model_raises_on_insufficient_data(tmp_path: Path):
    df = _make_features()
    db = get_db(tmp_path / "test.db")
    # Only 3 presence records — below threshold
    _add_obs(db, "Rare fish", _coords_at(df, range(5, 8)))

    with pytest.raises(ValueError, match="Insufficient"):
        train_species_model("Rare fish", db, df)


# ── predict_all_segments ─────────────────────────────────────────────────────


def test_predict_all_segments_returns_series(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Catostomus commersonii")

    result = train_species_model("Catostomus commersonii", db, df)
    preds = predict_all_segments(result, df)

    assert isinstance(preds, pd.Series)
    assert len(preds) == len(df)
    assert preds.index.name == "ogf_id"


def test_predict_all_segments_values_in_0_1(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Ambloplites rupestris")

    result = train_species_model("Ambloplites rupestris", db, df)
    preds = predict_all_segments(result, df)

    assert preds.between(0.0, 1.0).all(), "Some probabilities outside [0, 1]"


def test_calibration_improves_the_brier_score(tmp_path: Path):
    """Calibration is worth having only if the probabilities get better.

    This replaces an assertion that fewer than 5% of predictions sat at
    exactly 0.0 or 1.0. That test had the property backwards: isotonic
    regression is a step function fitted to the empirical rate, so producing
    exact 0.0 and 1.0 at the tails is what it is *supposed* to do. Exact zeros
    were evidence the calibrator was working, and the test read them as
    evidence it was not.

    It also failed by a single row — 76 of 80 interior against a `> 0.95`
    threshold — and it started failing when phase 3a added four features to
    the synthetic fixture, not when anything about the model changed. A
    knife-edge threshold on a property nobody wants is not coverage.

    What calibration actually promises is a probability you can read as a
    frequency, and the Brier score is the direct measure of that.
    """
    from sklearn.metrics import brier_score_loss

    from src.services.sdm_training import _build_base_pipeline, generate_pseudo_absences

    species = "Etheostoma caeruleum"
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, species)

    X_pres, _ = prepare_species_data(species, db, df)
    absence_ids = generate_pseudo_absences(X_pres.index.tolist(), df, db, ratio=2)
    X_abs = df.set_index("ogf_id").loc[absence_ids, _ALL_FEATURES]
    X_all = pd.concat([X_pres[_ALL_FEATURES], X_abs])
    y_all = np.concatenate([np.ones(len(X_pres)), np.zeros(len(X_abs))])

    uncalibrated = _build_base_pipeline()
    uncalibrated.fit(X_all, y_all)
    raw = uncalibrated.predict_proba(X_all)[:, 1]

    result = train_species_model(species, db, df)
    calibrated = result["model"].predict_proba(X_all)[:, 1]

    assert brier_score_loss(y_all, calibrated) <= brier_score_loss(y_all, raw), (
        "Calibration made the probabilities worse, which is the only thing it must not do"
    )


def test_calibrated_probabilities_are_monotonic_in_the_raw_score(tmp_path: Path):
    """Calibration may rescale the ranking, never reverse it.

    Isotonic regression is monotonic by construction, so a violation here
    means the calibrator was swapped for something that is not — which would
    silently change which segments rank highest while every range check in
    this file still passed.
    """
    from scipy.stats import spearmanr

    from src.services.sdm_training import _build_base_pipeline, generate_pseudo_absences

    species = "Etheostoma caeruleum"
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, species)

    X_pres, _ = prepare_species_data(species, db, df)
    absence_ids = generate_pseudo_absences(X_pres.index.tolist(), df, db, ratio=2)
    X_abs = df.set_index("ogf_id").loc[absence_ids, _ALL_FEATURES]
    X_all = pd.concat([X_pres[_ALL_FEATURES], X_abs])
    y_all = np.concatenate([np.ones(len(X_pres)), np.zeros(len(X_abs))])

    uncalibrated = _build_base_pipeline()
    uncalibrated.fit(X_all, y_all)
    raw = uncalibrated.predict_proba(X_all)[:, 1]

    result = train_species_model(species, db, df)
    calibrated = result["model"].predict_proba(X_all)[:, 1]

    rho, _ = spearmanr(raw, calibrated)
    assert rho > 0.0, f"Calibration inverted the ranking (Spearman {rho:.3f})"


# ── spatial CV: unvalidated must not read as a score ──────────────────────────


def test_spatial_cv_returns_none_not_half_when_no_fold_is_usable():
    """The regression this guards.

    _spatial_block_cv used to return a bare 0.5 both for "genuinely chance"
    and for "nothing could be evaluated". A survey-absence experiment reported
    0.5000 and was nearly written up as "true absences perform worse"; every
    fold had in fact been skipped.
    """
    from src.services.sdm_training import _spatial_block_cv

    df = _make_features(n=40)
    # all one class -> every fold fails the presence/absence checks
    from src.services.sdm_training import _extract_features

    X = _extract_features(df)
    y = np.ones(len(X))

    res = _spatial_block_cv(X, y, X.index.tolist(), df)

    assert res.auc is None, "unevaluable CV must not report a number"
    assert res.evaluable is False
    assert res.folds_used == 0
    assert sum(res.skipped.values()) > 0, "skips must be counted, not swallowed"
    assert "not evaluable" in res.describe()


def test_spatial_cv_skips_single_class_training_fold():
    """A fold whose TRAIN side is one class fits a constant and scores exactly
    0.5. The first version checked only the test side, so this looked real."""
    from src.services.sdm_training import _extract_features, _spatial_block_cv

    df = _make_features(n=60)
    X = _extract_features(df)
    y = np.ones(len(X))

    # Every absence in one quadrant, but leave some presences there too. The
    # test side then holds both classes and passes its checks, while the train
    # side (the other three quadrants) is all presence. That is the shape the
    # survey-absence experiment hit: absences clustered in one region because
    # only that region had been re-ingested.
    lats, lngs = df["centroid_lat"].values, df["centroid_lng"].values
    lat_mid = (lats.max() + lats.min()) / 2
    lng_mid = (lngs.max() + lngs.min()) / 2
    sw = np.flatnonzero((lats < lat_mid) & (lngs < lng_mid))
    assert len(sw) >= 8, f"fixture must populate the SW quadrant, got {len(sw)}"
    y[sw[: len(sw) - 3]] = 0.0  # keep 3 presences in SW so the test side is mixed

    res = _spatial_block_cv(X, y, X.index.tolist(), df)

    assert "no_absences_in_train" in res.skipped, (
        f"single-class train fold not detected; skipped={res.skipped}"
    )
    assert res.auc is None, "a run with no usable fold must not report a score"


def test_spatial_cv_reports_partial_evaluation():
    """Scoring on some folds is fine, but the caller is told how many."""
    from src.services.sdm_training import _extract_features, _spatial_block_cv

    df = _make_features(n=80)
    X = _extract_features(df)
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, len(X)).astype(float)

    res = _spatial_block_cv(X, y, X.index.tolist(), df)

    assert res.folds_used + sum(res.skipped.values()) <= 4
    if res.evaluable:
        assert 0.0 <= res.auc <= 1.0
        assert f"{res.folds_used}/4" in res.describe()


def test_train_species_model_carries_the_cv_reason(tmp_path: Path):
    df = _make_features()
    db = _setup_smoke_db(tmp_path, df, "Ambloplites rupestris")

    result = train_species_model("Ambloplites rupestris", db, df)

    assert "spatial_cv_folds_used" in result
    assert "spatial_cv_skipped" in result
    assert "spatial_cv_note" in result
    auc = result["spatial_cv_auc"]
    assert auc is None or isinstance(auc, float)
    # the note and the value must agree about whether this was validated
    assert (auc is None) == ("not evaluable" in result["spatial_cv_note"])
