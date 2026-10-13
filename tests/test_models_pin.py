"""Validation rules of the pin request and record models."""

import pytest
from pydantic import ValidationError

from src.models.pin import (
    Pin,
    PinCreate,
    PinUpdate,
    RecalledVisit,
    RecalledVisitCreate,
    SessionUpdate,
    TripPinRating,
)


def test_pin_create_strips_name_and_rejects_blank():
    assert PinCreate(name="  Home pool ", lat=43.5, lng=-79.7).name == "Home pool"
    with pytest.raises(ValidationError):
        PinCreate(name="   ", lat=43.5, lng=-79.7)


@pytest.mark.parametrize("lat, lng", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_pin_create_rejects_impossible_coordinates(lat, lng):
    with pytest.raises(ValidationError):
        PinCreate(name="x", lat=lat, lng=lng)


def test_pin_create_rejects_unknown_fields():
    # A client cannot smuggle in an owner, a state or a provenance.
    for extra in ({"user_id": 2}, {"state": "visited"}, {"provenance": "shared"}):
        with pytest.raises(ValidationError):
            PinCreate(name="x", lat=43.5, lng=-79.7, **extra)


def test_pin_update_tracks_what_was_sent_and_allows_clearing_notes():
    update = PinUpdate(notes=None)
    assert update.model_dump(exclude_unset=True) == {"notes": None}
    assert PinUpdate().model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("field", ["name", "is_favourite"])
def test_pin_update_cannot_clear_name_or_favourite(field):
    with pytest.raises(ValidationError):
        PinUpdate(**{field: None})


@pytest.mark.parametrize("rating", [0, 6, -1])
def test_ratings_outside_the_five_anchors_are_rejected(rating):
    with pytest.raises(ValidationError):
        SessionUpdate(rating=rating)
    with pytest.raises(ValidationError):
        TripPinRating(rating=rating)
    with pytest.raises(ValidationError):
        RecalledVisitCreate(rating=rating)


def test_a_skipped_rating_is_null():
    assert TripPinRating(rating=None).rating is None
    assert RecalledVisitCreate().rating is None


def test_session_times_need_a_timezone_and_must_end_after_they_start():
    ok = SessionUpdate(started_at="2026-06-10T08:00:00Z", ended_at="2026-06-10T10:00:00+00:00")
    assert (ok.ended_at - ok.started_at).total_seconds() == 7200
    with pytest.raises(ValidationError):
        SessionUpdate(started_at="2026-06-10T08:00:00")  # naive
    with pytest.raises(ValidationError):
        SessionUpdate(started_at="2026-06-10T10:00:00Z", ended_at="2026-06-10T08:00:00Z")


def test_recalled_species_are_trimmed_and_deduplicated():
    visit = RecalledVisitCreate(species=[" Brook Trout", "brook trout", "", "Creek Chub"])
    assert visit.species == ["Brook Trout", "Creek Chub"]


def test_recalled_record_is_always_provenance_recalled():
    assert RecalledVisit(id=1, pin_id=1).provenance == "recalled"
    with pytest.raises(ValidationError):
        RecalledVisit(id=1, pin_id=1, provenance="logged")


def test_pin_record_has_no_shared_provenance():
    base = dict(id=1, user_id=1, name="x", lat=43.5, lng=-79.7, jurisdiction="CA-ON")
    assert Pin(**base).provenance == "user"
    assert Pin(**base, provenance="imported").provenance == "imported"
    with pytest.raises(ValidationError):
        Pin(**base, provenance="shared")


# -- PinConditionRow (the conditions panel) ---------------------------------------


def _row(**kw):
    from src.models.pin import PinConditionRow

    base = {"field": "flow_cms", "label": "River flow", "group": "water_flow"}
    return PinConditionRow(**{**base, **kw})


def test_condition_row_needs_a_value_or_a_reason_never_both_or_neither():
    assert _row(value=1.2, unit="m³/s").empty_reason is None
    assert _row(empty_reason="no_live_source").value is None
    with pytest.raises(ValidationError):
        _row()
    with pytest.raises(ValidationError):
        _row(value=1.2, empty_reason="no_live_source")


def test_condition_row_keeps_a_zero_value():
    """0 °C and 0 mm are readings. Only None is absence."""
    assert _row(value=0.0).value == 0.0


def test_condition_row_carries_source_station_distance_and_age():
    row = _row(
        value=0.105,
        source="WSC station 02HB027",
        station="FOURTEEN MILE CREEK AT OAKVILLE",
        distance_km=3.0,
        observed_at="2026-10-13T15:05:00+00:00",
        age_s=3618,
        stale=False,
    )
    assert row.station == "FOURTEEN MILE CREEK AT OAKVILLE" and row.distance_km == 3.0


def test_condition_row_stale_defaults_to_false_and_trend_values_are_words():
    assert _row(value="rising").stale is False
    assert _row(value="rising").value == "rising"
