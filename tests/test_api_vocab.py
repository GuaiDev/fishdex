"""The API speaks the spots vocabulary: no stretch, anchor, confluence or tailwater.

The tables, stored JSON and internal modules keep those words. Only what a
client sees is checked here: every field name, and every string a person could
read, in the committed response fixtures and in live detail responses built
from rows that still carry the stored names.

Scope: the spots API only. The legacy `/map/stretches`,
`/map/stretches/{stretch_id}/anchors` and `/map/segments` endpoints serve the
old web AnchorSheet and five-type legend, keep their old vocabulary on
purpose, and are excluded until they are removed with the AnchorSheet.
"""

import json
import re
from pathlib import Path

from src.models.water_body import WaterBody
from src.services import api_vocab, terms
from src.services.spots import get_spot_detail
from src.services.water_bodies import get_water_body_detail
from src.storage.spots import replace_spots
from src.storage.water_bodies import write_water_bodies
from tests.test_spot_card import junction, spot
from tests.test_spot_card import walk as _walk

RETIRED = re.compile(r"(stretch|anchor|confluence|tailwater)", re.IGNORECASE)

# Keys whose values are ids, source refs or term ids rather than words a person
# reads: an id slug is derived from a source name and may contain a retired word.
# Term ids are kept on purpose; they are how literacy is tracked.
ID_KEYS = {"term_id", "term_ids", "ref", "merged_record_ids", "cache_key"}
FIXTURES = Path(__file__).parent / "fixtures"


def field_names(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from field_names(item)
    elif isinstance(value, list):
        for item in value:
            yield from field_names(item)


def user_facing_strings(value):
    return _walk(value, skip=ID_KEYS)


def assert_clean(payload, where):
    bad_names = sorted({n for n in field_names(payload) if RETIRED.search(n)})
    assert not bad_names, f"{where}: retired word in field name {bad_names}"
    for text in user_facing_strings(payload):
        assert not RETIRED.search(text), f"{where}: retired word in {text!r}"


def test_no_retired_word_in_any_spots_fixture_response():
    entries = json.loads((FIXTURES / "spots_api_responses.json").read_text())["requests"]
    assert len(entries) > 10
    for entry in entries:
        assert_clean(entry["response"], entry["request"])
        assert_clean(entry["note"], entry["request"])


def test_no_retired_word_in_the_profile_fixture_outside_term_ids():
    payload = json.loads((FIXTURES / "profile_api_responses.json").read_text())
    # Concept state is keyed by term id, which is kept; only values are read here.
    for text in user_facing_strings(payload):
        assert not RETIRED.search(text), text


def test_a_stored_spot_is_served_in_spots_words(db):
    stored = junction()
    assert stored.structure_type == "confluence"
    assert "stretch_upstream_km" in stored.structure_attrs  # the stored names are untouched
    dam = spot(
        "below_dam",
        lat=43.6,
        attrs={"dam_kind": "weir", "tailwater_verified": True},
    )
    replace_spots(db, "CA-ON", [stored, dam])
    for s in (stored, dam):
        detail = get_spot_detail(db, s.spot_id)
        assert_clean(detail, s.spot_id)
    detail = get_spot_detail(db, stored.spot_id)
    assert detail["structure_type"] == "junction"
    assert detail["structure_attrs"]["river_upstream_km"] == 71.0
    assert detail["structure_attrs"]["river_flows_on"] is True
    assert get_spot_detail(db, dam.spot_id)["structure_attrs"]["below_dam_verified"] is True


def test_a_stored_water_body_is_served_in_spots_words(db):
    wb = WaterBody(
        water_body_id="river:credit-river",
        jurisdiction="CA-ON",
        name="Credit River",
        name_source="curated",
        type="river",
        geometry={"type": "LineString", "coordinates": [[-79.6, 43.5], [-79.7, 43.6]]},
        geometry_source="stretch_union",
        stretch_ids=["credit-lower"],
        built_at="2026-01-01",
    )
    write_water_bodies(db.conn, "CA-ON", [wb])
    db.conn.commit()
    detail = get_water_body_detail(db, wb.water_body_id)
    assert detail["section_ids"] == ["credit-lower"]
    assert detail["geometry_source"] == "section_union"
    assert_clean(detail, wb.water_body_id)


def test_source_rows_are_renamed_in_a_spot_s_sources():
    from src.models.spot import SpotSource

    s = junction().model_copy(
        update={
            "sources": [
                SpotSource(kind="osm", ref="way/1", anchor_ids=["a:b"], built_at="2026-01-01")
            ]
        }
    )
    out = api_vocab.spot_for_api(s.model_dump())
    assert out["sources"][0]["merged_record_ids"] == ["a:b"]
    assert "anchor_ids" not in out["sources"][0]


def test_the_translation_does_not_change_its_input():
    s = junction()
    before = s.model_dump()
    api_vocab.spot_for_api(s.model_dump())
    assert s.model_dump() == before


def test_the_checker_catches_each_kind_of_leak():
    for leaky in (
        {"stretch_ids": []},
        {"a": {"sources": [{"anchor_ids": []}]}},
        {"structure_type": "confluence"},
        {"geometry_source": "stretch_union"},
        {"title": "the tailwater below the dam"},
    ):
        try:
            assert_clean(leaky, "leaky")
        except AssertionError:
            continue
        raise AssertionError(f"not caught: {leaky}")


def test_the_term_registry_keeps_its_ids_and_loses_the_retired_words():
    """The ids stay (literacy evidence is stored against them); no wording shows them."""
    assert {"confluence", "tailwater"} <= set(terms.TERMS)
    for term_id, term in terms.TERMS.items():
        assert term["term_id"] == term_id
        for field in ("plain", "technical", "definition"):
            assert not RETIRED.search(term[field]), (term_id, field, term[field])


def test_the_labels_and_terms_payloads_say_none_of_the_retired_words():
    from src.services import spot_labels

    for payload in (spot_labels.labels_payload(), terms.terms_payload()):
        assert_clean(payload, "labels payload")
