"""Tests for vision_screening module. All mock Mapbox and Claude calls — no live API calls."""

import httpx

import src.services.vision_screening as vs_mod

# ── mock helpers ──────────────────────────────────────────────────────────────


class _FakeTileResponse:
    status_code = 200
    content = b"FAKE_PNG_BYTES"


def _mock_tile(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeTileResponse())


def _mock_vision(monkeypatch, text: str):
    class _Content:
        pass

    obj = _Content()
    obj.text = text

    class _Response:
        pass

    resp = _Response()
    resp.content = [obj]

    monkeypatch.setattr(vs_mod.llm, "complete", lambda *a, **kw: resp)


# ── screen_segment tests ──────────────────────────────────────────────────────


def test_screen_segment_no_water(monkeypatch):
    """Vision response indicating no water → verdict 'unlikely'."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: no\n2. TYPE: engineered drainage\n3. ACCESS: no\n"
        "4. STRUCTURE: none visible\n5. VERDICT: no",
    )

    result = vs_mod.screen_segment(43.5, -79.5, 2, "Test Creek", False, user_id=1)

    assert result["screened"] is True
    assert result["verdict"] == "unlikely"


def test_screen_segment_culvert(monkeypatch):
    """Vision response mentioning culvert → is_culvert_crossing=True."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: unclear\n2. TYPE: culvert crossing\n3. ACCESS: no\n"
        "4. STRUCTURE: culvert crossing visible under road\n5. VERDICT: maybe",
    )

    result = vs_mod.screen_segment(43.5, -79.5, 3, None, False, user_id=1)

    assert result["screened"] is True
    assert result["is_culvert_crossing"] is True
    assert result["verdict"] == "possible"


def test_screen_segment_houses(monkeypatch):
    """Vision response indicating houses → access_blocked_by_structures=True."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: yes\n2. TYPE: natural stream\n3. ACCESS: yes, houses directly adjacent\n"
        "4. STRUCTURE: none visible\n5. VERDICT: maybe",
    )

    result = vs_mod.screen_segment(43.5, -79.5, 3, "Bronte Creek", False, user_id=1)

    assert result["screened"] is True
    assert result["access_blocked_by_structures"] is True
    assert result["verdict"] == "possible"


def test_screen_segment_golf_course(monkeypatch):
    """Vision response mentioning golf course → is_golf_course=True, access_blocked=True."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: yes\n2. TYPE: natural stream\n"
        "3. ACCESS: yes, golf course fairway borders the stream\n"
        "4. STRUCTURE: none visible\n5. VERDICT: maybe",
    )

    result = vs_mod.screen_segment(43.5, -79.5, 3, None, False, user_id=1)

    assert result["screened"] is True
    assert result["is_golf_course"] is True
    assert result["access_blocked_by_structures"] is True


def test_screen_segment_satellite_unavailable(monkeypatch):
    """Failed Mapbox fetch → screened=False, verdict=unverified."""

    def _raise(*a, **kw):
        raise Exception("timeout")

    monkeypatch.setattr(httpx, "get", _raise)

    result = vs_mod.screen_segment(43.5, -79.5, 3, None, False, user_id=1)

    assert result["screened"] is False
    assert result["verdict"] == "unverified"
    assert result["vision_note"] is None


def test_screen_segment_promising(monkeypatch):
    """Vision response with 'yes' verdict → verdict 'promising'."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: yes\n2. TYPE: natural stream\n3. ACCESS: no\n"
        "4. STRUCTURE: confluence visible\n5. VERDICT: yes",
    )

    result = vs_mod.screen_segment(43.5, -79.5, 4, "Credit River", True, user_id=1)

    assert result["screened"] is True
    assert result["verdict"] == "promising"


# ── screen_candidates tests ───────────────────────────────────────────────────


def _make_candidates(n: int) -> list[dict]:
    return [
        {
            "ogf_id": i,
            "centroid_lat": 43.5 + i * 0.01,
            "centroid_lng": -79.5,
            "stream_order": 3,
            "watercourse_name": None,
            "is_confluence_segment": False,
        }
        for i in range(n)
    ]


def test_screen_candidates_filters_unlikely(monkeypatch):
    """3 candidates — 1 returns 'unlikely' → only 2 returned."""
    _mock_tile(monkeypatch)

    call_count = 0

    def _varying_vision(**kw):
        nonlocal call_count

        class _C:
            pass

        class _R:
            pass

        texts = [
            # candidate 0 → promising
            "1. WATER: yes\n2. TYPE: natural stream\n"
            "3. ACCESS: no\n4. STRUCTURE: none\n5. VERDICT: yes",
            # candidate 1 → unlikely (filtered)
            "1. WATER: no\n2. TYPE: engineered drainage\n"
            "3. ACCESS: no\n4. STRUCTURE: none\n5. VERDICT: no",
            # candidate 2 → possible
            "1. WATER: yes\n2. TYPE: natural stream\n"
            "3. ACCESS: partial\n4. STRUCTURE: none\n5. VERDICT: maybe",
        ]
        c = _C()
        c.text = texts[call_count % len(texts)]
        call_count += 1
        r = _R()
        r.content = [c]
        return r

    monkeypatch.setattr(vs_mod.llm, "complete", lambda *a, **kw: _varying_vision(**kw))

    candidates = _make_candidates(3)
    result = vs_mod.screen_candidates(candidates, max_screens=10, user_id=1)

    assert len(result) == 2
    assert result[0]["ogf_id"] == 0
    assert result[1]["ogf_id"] == 2
    assert result[0]["vision_screening"]["verdict"] == "promising"
    assert result[1]["vision_screening"]["verdict"] == "possible"


def test_vision_budget(monkeypatch):
    """11 candidates with max_screens=10 → last candidate unscreened but not skipped."""
    _mock_tile(monkeypatch)
    _mock_vision(
        monkeypatch,
        "1. WATER: yes\n2. TYPE: natural stream\n3. ACCESS: no\n"
        "4. STRUCTURE: none\n5. VERDICT: yes",
    )

    candidates = _make_candidates(11)
    result = vs_mod.screen_candidates(candidates, max_screens=10, user_id=1)

    assert len(result) == 11

    last = result[-1]
    assert last["vision_screening"]["screened"] is False
    assert last["vision_screening"]["reason"] == "vision budget exhausted"
    assert last["vision_screening"]["verdict"] == "unverified"

    # First 10 were actually screened
    for i in range(10):
        assert result[i]["vision_screening"]["screened"] is True
