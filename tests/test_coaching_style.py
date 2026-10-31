"""Coaching follows the angler's inferred style, and only when asked openly.

As in test_coaching.py, the prompt is the thing under test: these assert what
reaches the model, not what comes back.
"""

from src.models.angler_profile import AnglerProfile, BlendedValue, ProfileValue, SignalReading
from src.services.context.render import render_angler_style
from src.storage import angler_profile as store
from tests.test_angler_profile import trip
from tests.test_coaching import captured, db_conn  # noqa: F401  (fixtures)

BLANK = BlendedValue(value=0.5, confidence=0.0)


def _profile(*readings: SignalReading) -> AnglerProfile:
    return AnglerProfile(
        user_id=1,
        orientation=BLANK,
        size_focus=BLANK,
        micro_focus=BLANK,
        signals={r.signal_id: r for r in readings},
    )


def _explore(value, confidence=0.8, declared=False):
    kw = {"declared": ProfileValue(value=value, source="declared", weight=1.0)} if declared else {}
    return SignalReading(
        signal_id="explore", kind="scalar", value=value, confidence=confidence, **kw
    )


def _water(shares, confidence=0.8):
    return SignalReading(
        signal_id="water_type", kind="distribution", shares=shares, confidence=confidence
    )


# -- render_angler_style alone -------------------------------------------------


def test_nothing_active_says_style_unknown_and_assumes_none():
    for profile in (None, _profile(), _profile(_explore(0.9, confidence=0.2))):
        text = render_angler_style(profile)
        assert "Style is not known yet" in text
        assert "scouting" not in text and "controlled step" not in text


def test_explorer_regular_and_mixed_get_their_own_brief():
    assert "try new water" in render_angler_style(_profile(_explore(0.85)))
    assert "same water" in render_angler_style(_profile(_explore(0.15)))
    mixed = render_angler_style(_profile(_explore(0.5)))
    assert "exactly one" in mixed and "Never offer both" in mixed
    assert "try new water" not in mixed and "same water" not in mixed


def test_water_type_renders_only_when_active_and_dominant():
    assert "creeks" in render_angler_style(_profile(_water({"creek": 0.8, "pond": 0.2})))
    assert "They mostly go to" not in render_angler_style(
        _profile(_water({"creek": 0.4, "pond": 0.3, "lake": 0.3}))
    )
    assert "They mostly go to" not in render_angler_style(
        _profile(_water({"creek": 1.0}, confidence=0.1))
    )


def test_declared_reading_is_marked_for_the_coach_but_not_to_be_surfaced():
    text = render_angler_style(_profile(_explore(0.9, confidence=0.4, declared=True)))
    assert "set this themselves" in text
    assert "Do not tell the angler which style you assumed" in text


def test_style_text_names_no_jurisdiction_and_makes_no_promises():
    text = render_angler_style(_profile(_explore(0.9), _water({"river": 1.0})))
    for banned in ("Ontario", "Canada", "MNRF", "CA-ON", "province", "state of"):
        assert banned not in text
    assert "Never promise or predict size or catch outcomes" in text
    assert "directly" in text


# -- what reaches the model ----------------------------------------------------


def _log_explorer(db):
    for i in range(12):
        trip(db, 1, f"2025-06-{i + 1:02d}", 43.0 + i * 0.3, -79.0, name=f"creek {i}")


def _log_regular(db):
    for i in range(12):
        trip(db, 1, f"2025-06-{i + 1:02d}", 43.5, -79.5, name="home creek")


def test_explorer_log_reaches_the_species_prompt(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_species_coaching

    _log_explorer(db_conn)
    get_species_coaching(db_conn, "creek chub", "What should I look at on a new creek?")
    assert "try new water" in captured["prompt"]
    assert "answered directly" in captured["prompt"]


def test_regular_log_reaches_the_species_prompt(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_species_coaching

    _log_regular(db_conn)
    get_species_coaching(db_conn, "creek chub")
    assert "same water" in captured["prompt"]


def test_thin_log_says_style_unknown(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_species_coaching

    trip(db_conn, 1, "2025-06-01", 43.0, -79.0, name="one creek")
    get_species_coaching(db_conn, "creek chub")
    assert "Style is not known yet" in captured["prompt"]


def test_explicit_questions_win_for_both_styles(db_conn, captured):  # noqa: F811
    """Explorer asks about a known spot; regular asks for somewhere new."""
    from src.services.coaching import get_species_coaching

    _log_explorer(db_conn)
    get_species_coaching(db_conn, "creek chub", "Tell me about creek 3 again.")
    prompt = captured["prompt"]
    assert "Tell me about creek 3 again." in prompt
    assert "answered directly, with this style left out" in prompt

    for t in ("stops", "sessions", "catches"):
        db_conn[t].delete_where()
    _log_regular(db_conn)
    get_species_coaching(db_conn, "creek chub", "Where should I try somewhere new?", user_id=1)
    prompt = captured["prompt"]
    assert "Where should I try somewhere new?" in prompt
    assert "asking for somewhere new, whatever their usual habit" in prompt


def test_declared_override_reaches_the_prompt(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_species_coaching

    store.set_declared(db_conn, 1, "signal:explore", 0.1, 1.0, is_override=True)
    get_species_coaching(db_conn, "creek chub")
    assert "same water" in captured["prompt"]
    assert "set this themselves" in captured["prompt"]


def test_location_prompt_carries_style_and_rules(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_location_coaching

    _log_regular(db_conn)
    db_conn["water_features"].insert(
        {"osm_id": "w1", "feature_type": "stream", "name": "Home Creek",
         "lat": 43.5, "lng": -79.5},
        alter=True,
    )  # fmt: skip
    get_location_coaching(db_conn, "Home Creek")
    prompt = captured["prompt"]
    assert "same water" in prompt
    assert "Never promise or predict size or catch outcomes" in prompt


def test_default_question_is_outcome_neutral(db_conn, captured):  # noqa: F811
    from src.services.coaching import get_species_coaching

    get_species_coaching(db_conn, "creek chub")
    assert "catch more" not in captured["prompt"]
    assert "What could I try next with creek chub?" in captured["prompt"]


def test_listed_species_still_refuses_whatever_the_style(db_conn, captured, monkeypatch):  # noqa: F811
    import src.services.coaching as coaching
    from src.models.context import ContextField, SpeciesContext

    monkeypatch.setattr(
        coaching,
        "describe_species",
        lambda db, name: SpeciesContext(
            species=name,
            habitat_note=ContextField.inferred("cool headwater streams"),
            sar_alert=True,
            status_known_listed=True,
            sar_reason="Listed: Endangered.",
        ),
    )
    _log_explorer(db_conn)
    coaching.get_species_coaching(db_conn, "Redside Dace")
    prompt = captured["prompt"]
    assert "CONSERVATION OVERRIDE" in prompt
    assert prompt.rindex("CONSERVATION OVERRIDE") > prompt.index("Angler style")


def test_rules_carry_the_no_unseen_species_line():
    from src.services.coaching import _RULES

    assert "any unseen species" in _RULES
