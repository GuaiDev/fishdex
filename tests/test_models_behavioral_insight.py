"""Tests for the BehavioralInsight Pydantic model."""

import pytest
from pydantic import ValidationError

from src.models.behavioral_insight import BehavioralInsight


def _valid_kwargs(**overrides):
    base = dict(
        user_id=1,
        species="brook trout",
        condition_type="behavioral",
        condition_context="post-cold-front",
        conclusion="Brook trout feed aggressively post-cold-front in streams under 15C.",
        source_type="trip_log",
        source_detail="8 personal outings Credit River spring 2026",
        evidence_count=8,
    )
    base.update(overrides)
    return base


def test_valid_model():
    insight = BehavioralInsight(**_valid_kwargs())
    assert insight.species == "brook trout"
    assert insight.version == 1
    assert insight.is_current is True
    assert insight.user_verified is False
    assert insight.confidence == "unverified"
    assert insight.contradicted_by is None


def test_defaults():
    insight = BehavioralInsight(**_valid_kwargs())
    assert insight.id is None
    assert insight.jurisdiction is None
    assert insight.evidence_count == 8


def test_empty_conclusion_raises():
    with pytest.raises(ValidationError, match="conclusion must not be empty"):
        BehavioralInsight(**_valid_kwargs(conclusion="   "))


def test_invalid_confidence_raises():
    with pytest.raises(ValidationError):
        BehavioralInsight(**_valid_kwargs(confidence="certain"))  # type: ignore[arg-type]


def test_invalid_condition_type_raises():
    with pytest.raises(ValidationError):
        BehavioralInsight(**_valid_kwargs(condition_type="vibes"))  # type: ignore[arg-type]


def test_invalid_source_type_raises():
    with pytest.raises(ValidationError):
        BehavioralInsight(**_valid_kwargs(source_type="crystal_ball"))  # type: ignore[arg-type]


def test_all_confidence_values():
    for conf in ("high", "medium", "low", "unverified"):
        i = BehavioralInsight(**_valid_kwargs(confidence=conf))
        assert i.confidence == conf


def test_all_condition_types():
    for ct in ("behavioral", "habitat", "temporal", "gear"):
        i = BehavioralInsight(**_valid_kwargs(condition_type=ct))
        assert i.condition_type == ct


def test_jurisdiction_optional():
    i = BehavioralInsight(**_valid_kwargs(jurisdiction="CA-ON"))
    assert i.jurisdiction == "CA-ON"

    i2 = BehavioralInsight(**_valid_kwargs())
    assert i2.jurisdiction is None
