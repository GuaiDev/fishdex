"""The term registry: one id per map/habitat concept, factual definitions only."""

import re

from src.services import spot_labels, terms

# A definition says what a thing is. Anything that reads as a fishing verdict
# ("fish hold here", "good spot", "productive") belongs nowhere in it.
VERDICT = re.compile(
    r"\b(fish|fishing|catch|bite|bites|good|great|best|productive|target)\b",
    re.IGNORECASE,
)


def test_the_concepts_the_product_names_all_have_a_term_id():
    for term_id in ("dam", "confluence", "culvert", "river_mouth", "drop_off", "weed_edge"):
        assert term_id in terms.TERMS


def test_every_term_has_all_three_wordings_and_its_own_id():
    for term_id, t in terms.TERMS.items():
        assert t["term_id"] == term_id
        assert t["plain"] and t["technical"] and t["definition"]
        assert t["plain"] != t["definition"]


def test_definitions_are_factual_and_never_a_fishing_verdict():
    for t in terms.TERMS.values():
        assert not VERDICT.search(t["definition"]), t["term_id"]
        assert not VERDICT.search(t["plain"]), t["term_id"]


def test_every_spot_kind_and_shoreline_feature_maps_to_registered_terms():
    for kind in spot_labels.KIND_ORDER:
        for term_id in terms.SPOT_KIND_TERMS[kind]:
            assert term_id in terms.TERMS
    for kind in spot_labels.FEATURE_KIND_ORDER:
        assert terms.SHORELINE_KIND_TERMS[kind] == (kind,)


def test_a_weir_is_not_called_a_dam():
    assert terms.spot_terms("below_dam", dam_kind="weir") == ("weir", "tailwater")
    assert terms.spot_terms("below_dam", dam_kind="dam") == ("dam", "tailwater")
    assert terms.spot_terms("quiet_water") == ()


def test_the_labels_payload_carries_term_ids_not_presentation():
    payload = spot_labels.labels_payload()
    assert "glossary" not in payload
    assert [t["term_id"] for t in payload["terms"]] == list(terms.TERM_ORDER)
    kinds = {k["kind"]: k for k in payload["kinds"]}
    assert kinds["creek_junction"]["term_ids"] == ["confluence"]
    assert kinds["quiet_water"]["term_ids"] == []
    for feature in payload["shoreline_features"]:
        assert feature["term_ids"] == [feature["kind"]]


def test_card_linked_words_keep_their_old_definitions():
    """The three words cards link were moved here from translate.GLOSSARY unchanged."""
    assert terms.TERMS["confluence"]["definition"] == (
        "A confluence is the place where two streams meet and become one."
    )
    assert terms.TERMS["tailwater"]["definition"] == (
        "A tailwater is the part of a river just downstream of a dam."
    )
    assert set(terms.CARD_LINKED_TERMS) <= set(terms.TERMS)
