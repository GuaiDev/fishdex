"""Tests for the records escalation rung.

No live calls: a fake client returns canned content blocks. What is asserted is
the semantics — what provenance the results carry, and which empty reason comes
back — not that a string was produced.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.models.context import EmptyReason, ProvenanceKind
from src.services.context import escalation
from src.services.context.escalation import escalate_records


class _FakeClient:
    """Returns one canned reply, or raises, and records what it was asked."""

    def __init__(self, text: str | None = None, error: Exception | None = None):
        self._text = text
        self._error = error
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._text or "")])


def _call(client, **kwargs):
    with patch("src.llm.client.get_client", return_value=client):
        return escalate_records(
            place_name="Sixteen Mile Creek",
            lat=43.4675,
            lng=-79.6877,
            user_id=1,
            **kwargs,
        )


# -- the blocklist -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.fishbrain.com/spots/1",
        "https://m.facebook.com/groups/x",
        "https://instagram.com/p/abc",
        "https://vm.tiktok.com/x",
    ],
)
def test_forbidden_sources_are_blocked(url):
    assert escalation.is_blocked(url)


def test_lookalike_domain_is_not_blocked():
    assert not escalation.is_blocked("https://notfishbrain.com/x")


def test_blocked_result_never_becomes_a_record():
    client = _FakeClient(
        '[{"species": "Brown Trout", "url": "https://fishbrain.com/x", '
        '"source": "FishBrain", "date": "2024-05-01"}]'
    )
    records, reason = _call(client)
    assert records == []
    assert reason is EmptyReason.WEB_SEARCH_EMPTY


# -- provenance ----------------------------------------------------------------


def test_web_records_are_tagged_web_and_never_verified():
    client = _FakeClient(
        '[{"species": "Redside Dace", "url": "https://trca.ca/report", '
        '"source": "TRCA 2023 survey", "date": "2023-07-14"}]'
    )
    records, reason = _call(client)
    assert reason is None
    assert len(records) == 1
    prov = records[0].provenance
    assert prov.kind is ProvenanceKind.WEB
    assert prov.verified is False
    assert prov.url == "https://trca.ca/report"


def test_entry_without_a_url_is_dropped():
    """A WEB claim with no URL is indistinguishable from an invented one."""
    client = _FakeClient('[{"species": "Brook Trout", "source": "some forum"}]')
    records, reason = _call(client)
    assert records == []
    assert reason is EmptyReason.WEB_SEARCH_EMPTY


def test_duplicate_species_collapse():
    client = _FakeClient(
        '[{"species": "Creek Chub", "url": "https://a.example/1"},'
        ' {"species": "creek chub", "url": "https://b.example/2"}]'
    )
    records, _ = _call(client)
    assert len(records) == 1


# -- the empty reasons are distinct --------------------------------------------


def test_empty_array_is_web_search_empty_not_a_failure():
    records, reason = _call(_FakeClient("[]"))
    assert records == []
    assert reason is EmptyReason.WEB_SEARCH_EMPTY


def test_api_error_is_a_transient_failure_not_an_absence():
    records, reason = _call(_FakeClient(error=RuntimeError("503")))
    assert records == []
    assert reason is EmptyReason.LIVE_LOOKUP_FAILED


def test_unparseable_output_is_a_failure_not_an_absence():
    """Garbage output and "the web has nothing" have different remedies."""
    records, reason = _call(_FakeClient("I could not find anything, sorry."))
    assert records == []
    assert reason is EmptyReason.LIVE_LOOKUP_FAILED


# -- the request itself --------------------------------------------------------


def test_the_search_tool_is_actually_attached():
    client = _FakeClient("[]")
    _call(client)
    tools = client.calls[0]["tools"]
    assert any(t["type"] == "web_search_20250305" for t in tools)


def test_species_filter_reaches_the_prompt():
    client = _FakeClient("[]")
    _call(client, species_filter="Redside Dace")
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Redside Dace" in prompt
    assert "Sixteen Mile Creek" in prompt


# -- who is allowed to escalate ------------------------------------------------
#
# Escalation costs money on every call. The bundle decides, not the question,
# so these assert the wiring rather than the ladder.


@pytest.fixture
def db(tmp_path):
    from src.storage.database import get_db

    return fast_db


def _spy(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return [], EmptyReason.WEB_SEARCH_EMPTY

    monkeypatch.setattr("src.services.context.escalation.escalate_records", fake, raising=True)
    return calls


def test_map_tap_never_fires_a_live_search(db, monkeypatch):
    from src.services.context import describe

    calls = _spy(monkeypatch)
    ctx = describe(db, lat=43.4675, lng=-79.6877, caller="map_tap")
    assert ctx is not None
    assert ctx.records.escalated_to_web is False
    assert calls == []


def test_coach_escalates_when_the_corpus_is_empty(db, monkeypatch):
    from src.services.context import describe

    calls = _spy(monkeypatch)
    ctx = describe(db, lat=43.4675, lng=-79.6877, caller="coach")
    assert ctx is not None
    assert ctx.records.escalated_to_web is True
    assert len(calls) == 1


def test_explicit_flag_overrides_the_bundle(db, monkeypatch):
    from src.services.context import describe

    calls = _spy(monkeypatch)
    describe(db, lat=43.4675, lng=-79.6877, caller="coach", escalate=False)
    assert calls == []


def test_local_coverage_gap_survives_an_empty_web_search(db, monkeypatch):
    """ "We don't cover this area" is more informative than "the web had nothing"."""
    from src.services.context import describe

    _spy(monkeypatch)
    ctx = describe(db, lat=43.4675, lng=-79.6877, caller="coach")
    assert ctx.records.empty_reason is EmptyReason.SOURCE_DOES_NOT_COVER_AREA
