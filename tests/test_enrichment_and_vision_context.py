"""Two call sites wired to the context layer: trip_enrichment and species_vision.

Both changes are inputs-and-outputs only. The enrichment logic and the
human-confirm gate on photo species ID are untouched — the spec puts both out
of scope, and what changes here is what reaches a model and what comes back,
not what either pipeline does.
"""

import json

import pytest

from src.services.context.render import render_logged_stop, render_recorded_insight
from src.services.context.user import as_recorded_insight
from src.storage.database import get_db


@pytest.fixture
def db(tmp_path):
    return get_db(tmp_path / "wiring.db")


# -- a logged stop is not a database row ---------------------------------------


_ROW = {
    "id": 17,
    "session_id": 4,
    "user_id": 1,
    "location_name": "Byng Island",
    "location_text": "Byng Island",
    "date": "2025-06-14",
    "species_caught": json.dumps(["channel catfish"]),
    "was_productive": 1,
    "technique": "Santee Cooper rig",
    "gear": "cutbait",
    "water_clarity": "turbid",
    "water_level": None,
    "weather_notes": None,
    "time_of_day": "evening",
    "notes": "5lb channel cat at 10am",
    "photo_url": "/uploads/private/IMG_4471.jpg",
    "photo_lat": 42.9012,
    "photo_lng": -79.5988,
}


def test_internal_fields_never_reach_the_prompt():
    """json.dumps(stop) put row ids and photo EXIF coordinates in front of the model."""
    out = render_logged_stop(_ROW)
    for leaked in ("photo_url", "IMG_4471", "42.9012", "session_id", "user_id"):
        assert leaked not in out, f"{leaked} leaked into the rendered stop"


def test_the_anglers_observations_do_reach_it():
    out = render_logged_stop(_ROW)
    assert "Byng Island" in out
    assert "2025-06-14" in out
    assert "channel catfish" in out
    assert "Santee Cooper rig" in out
    assert "turbid" in out
    assert "5lb channel cat" in out


def test_unrecorded_is_stated_not_omitted():
    """ "we didn't write it down" and "it wasn't the case" are different facts."""
    out = render_logged_stop(_ROW)
    assert "water level unrecorded" in out
    assert "weather unrecorded" in out


def test_a_blank_stop_reads_as_a_blank_not_as_missing_data():
    row = dict(_ROW, species_caught=json.dumps([]), was_productive=0)
    assert "caught nothing" in render_logged_stop(row)


def test_an_unparseable_species_list_does_not_crash_the_render():
    row = dict(_ROW, species_caught="{not json")
    assert "caught nothing" in render_logged_stop(row)


# -- a stored insight carries where it came from -------------------------------


class _Insight:
    def __init__(self, source_type, source_detail="somewhere"):
        self.conclusion = "hold deeper above 24C"
        self.confidence = "medium"
        self.recommendation = None
        self.source_type = source_type
        self.source_detail = source_detail


def test_agent_synthesis_renders_as_reasoning():
    out = render_recorded_insight(as_recorded_insight(_Insight("agent_synthesis")))
    assert "reasoning, no source" in out


def test_a_survey_finding_does_not_render_as_reasoning():
    out = render_recorded_insight(as_recorded_insight(_Insight("mnrf_survey", "BsM 2019")))
    assert "reasoning, no source" not in out
    assert "BsM 2019" in out


def test_the_contradiction_prompt_uses_the_renderer(db, monkeypatch):
    """The whole point: no raw dict, and the insight's provenance travels."""
    from src.models.behavioral_insight import BehavioralInsight
    from src.services.trip_enrichment import _synthesize_nuanced_conclusion

    captured = {}

    def _complete(role, **kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return type("R", (), {"content": [type("B", (), {"text": "revised"})()]})()

    monkeypatch.setattr("src.services.trip_enrichment.llm.complete", _complete)

    insight = BehavioralInsight(
        species="channel catfish",
        condition_type="behavioral",
        condition_context="warm water",
        conclusion="hold deeper above 24C",
        confidence="medium",
        source_type="agent_synthesis",
        source_detail="assistant reasoning",
        evidence_count=2,
    )

    _synthesize_nuanced_conclusion(insight, _ROW, True, 1)
    prompt = captured["prompt"]

    assert "photo_url" not in prompt and "42.9012" not in prompt
    assert "Byng Island" in prompt
    assert "reasoning, no source" in prompt, "the insight's own provenance travels"
    assert "do not treat it as evidence that the condition was absent" in prompt


def test_no_client_still_degrades_gracefully():
    from src.models.behavioral_insight import BehavioralInsight
    from src.services.trip_enrichment import _synthesize_nuanced_conclusion

    insight = BehavioralInsight(
        species="x",
        condition_type="behavioral",
        condition_context="y",
        conclusion="original",
        confidence="low",
        source_type="trip_log",
        source_detail="log",
        evidence_count=1,
    )
    out = _synthesize_nuanced_conclusion(insight, _ROW, False, 1)
    assert out.startswith("original")


# -- a listed species can be suggested, and must be flagged --------------------


def _suggestion(*species):
    return {
        "screened": True,
        "candidates": [{"species": s, "confidence": "high"} for s in species],
        "unresolved": False,
        "note": None,
    }


def test_a_listed_species_is_flagged_after_identification(db, monkeypatch):
    import src.services.species_vision as sv
    from src.models.context import ContextField, SpeciesContext

    monkeypatch.setattr(
        sv,
        "describe_species",
        lambda d, name: SpeciesContext(
            species=name,
            habitat_note=ContextField(),
            sar_alert=True,
            status_known_listed=True,
            sar_reason="Listed: Endangered (source: COSEWIC).",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "src.services.context.describe_species",
        lambda d, name: SpeciesContext(
            species=name,
            sar_alert=True,
            status_known_listed=True,
            sar_reason="Listed: Endangered (source: COSEWIC).",
        ),
    )

    out = sv.annotate_conservation(db, _suggestion("Redside Dace"))
    assert out["any_candidate_listed"] is True
    assert out["candidates"][0]["conservation_status_known_listed"] is True
    assert "Endangered" in out["candidates"][0]["conservation_note"]
    assert "Keep it wet" in out["handling_note"]


def test_the_species_name_is_never_removed_from_the_suggestion(db, monkeypatch):
    """A photo of a Redside Dace is a Redside Dace. Dropping it makes the ID wrong."""
    import src.services.species_vision as sv
    from src.models.context import SpeciesContext

    monkeypatch.setattr(
        "src.services.context.describe_species",
        lambda d, name: SpeciesContext(
            species=name, sar_alert=True, status_known_listed=True, sar_reason="Listed."
        ),
    )
    out = sv.annotate_conservation(db, _suggestion("Redside Dace"))
    assert out["candidates"][0]["species"] == "Redside Dace"


def test_an_unlisted_species_gets_no_handling_note(db, monkeypatch):
    import src.services.species_vision as sv
    from src.models.context import SpeciesContext

    monkeypatch.setattr(
        "src.services.context.describe_species",
        lambda d, name: SpeciesContext(
            species=name,
            sar_alert=True,
            status_known_listed=False,
            sar_reason="Status not verified.",
        ),
    )
    out = sv.annotate_conservation(db, _suggestion("Creek Chub"))
    assert out["any_candidate_listed"] is False
    assert "handling_note" not in out


def test_a_failed_lookup_never_loses_the_identification(db, monkeypatch):
    import src.services.species_vision as sv

    monkeypatch.setattr(
        "src.services.context.describe_species",
        lambda d, name: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = sv.annotate_conservation(db, _suggestion("Creek Chub"))
    assert out["candidates"][0]["species"] == "Creek Chub"


def test_an_unresolved_photo_passes_through_untouched(db):
    import src.services.species_vision as sv

    empty = {"screened": True, "candidates": [], "unresolved": True, "note": None}
    assert sv.annotate_conservation(db, empty) is empty
    assert sv.annotate_conservation(db, None) is None
