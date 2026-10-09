"""The weekly ingest: area list as data, run through the service, failures counted."""

import json

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.models.ingest_area import IngestSource
from src.services import area_ingest
from src.services.area_ingest import DEFAULT_AREAS_PATH, load_areas


def test_committed_area_list_loads_and_matches_the_old_workflow():
    """The 33 curl steps of the retired GitHub Action became 25 areas."""
    areas = load_areas(DEFAULT_AREAS_PATH)
    assert len(areas) == 25
    assert sum(len(a.sources) for a in areas) == 35
    assert len({a.label for a in areas}) == 25
    by_label = {a.label: a for a in areas}
    bronte = by_label["Bronte Creek Oakville"]
    assert (bronte.jurisdiction, bronte.lat, bronte.lng, bronte.radius_km) == (
        "CA-ON",
        43.45,
        -79.72,
        25,
    )
    assert by_label["Miramichi River NB"].radius_for(IngestSource.TIDAL) == 100
    assert IngestSource.BC in by_label["Skeena River Terrace"].sources
    # All three configured AB areas run the AB bundle (FWMIS hydro since 2026-10-09)
    assert IngestSource.AB in by_label["Bow River Calgary"].sources
    assert IngestSource.AB in by_label["North Saskatchewan River Edmonton"].sources
    assert IngestSource.AB in by_label["Oldman River Lethbridge"].sources


def test_load_areas_rejects_a_malformed_area(tmp_path):
    p = tmp_path / "areas.json"
    p.write_text(json.dumps({"areas": [{"label": "x", "lat": 1, "lng": 2}]}))
    with pytest.raises(ValueError):
        load_areas(p)


def test_attempt_records_counts_and_failures_without_stopping():
    r = area_ingest.AreaIngestResult(label="x", source=IngestSource.GLOBAL)
    area_ingest._attempt(r, "first", lambda: 5)
    area_ingest._attempt(r, "flaky", lambda: 1 / 0)
    area_ingest._attempt(r, "bundle", lambda: {"a": 1, "b": 2})
    assert r.stored == {"first": 5, "a": 1, "b": 2}
    assert r.failed == {"flaky": "ZeroDivisionError: division by zero"}
    assert not r.ok


def test_run_area_dispatches_with_the_per_source_radius(monkeypatch):
    calls = []

    def fake(source):
        def _run(lat, lng, radius_km, label, *rest):
            calls.append((source, lat, lng, radius_km, label, rest))
            return area_ingest.AreaIngestResult(label=label, source=source)

        return _run

    monkeypatch.setattr(area_ingest, "run_global_ingest", fake(IngestSource.GLOBAL))
    monkeypatch.setitem(area_ingest._RUNNERS, IngestSource.TIDAL, fake(IngestSource.TIDAL))
    area = area_ingest.IngestArea(
        label="Annapolis River NS",
        jurisdiction="CA-NS",
        lat=44.98,
        lng=-65.5,
        radius_km=40,
        sources=["global", "tidal"],
        source_radius_km={"tidal": 100},
    )
    for s in area.sources:
        area_ingest.run_area(area, s, days_back=90)
    assert calls == [
        (IngestSource.GLOBAL, 44.98, -65.5, 40, "Annapolis River NS", (90,)),
        (IngestSource.TIDAL, 44.98, -65.5, 100, "Annapolis River NS", ()),
    ]


def _areas_file(tmp_path):
    p = tmp_path / "areas.json"
    p.write_text(
        json.dumps(
            {
                "areas": [
                    {
                        "label": "Credit River Mississauga",
                        "jurisdiction": "CA-ON",
                        "lat": 43.55,
                        "lng": -79.65,
                        "radius_km": 30,
                        "sources": ["global"],
                    },
                    {
                        "label": "Bow River Calgary",
                        "jurisdiction": "CA-AB",
                        "lat": 51.05,
                        "lng": -114.07,
                        "radius_km": 50,
                        "sources": ["global", "ab"],
                    },
                ]
            }
        )
    )
    return p


def test_weekly_ingest_dry_run_fetches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(area_ingest, "run_area", lambda *a, **k: pytest.fail("fetched"))
    out = CliRunner().invoke(
        app, ["weekly-ingest", "--areas", str(_areas_file(tmp_path)), "--dry-run"]
    )
    assert out.exit_code == 0, out.output
    assert "3 runs" in out.output


def test_weekly_ingest_runs_every_job_and_exits_nonzero_on_a_failure(tmp_path, monkeypatch):
    seen = []

    def fake_run_area(area, source, days_back=90):
        seen.append((area.label, source, days_back))
        r = area_ingest.AreaIngestResult(label=area.label, source=source, stored={"GBIF": 1})
        if source is IngestSource.AB:
            r.failed["AB adapters"] = "HTTPError: 503"
        return r

    monkeypatch.setattr(area_ingest, "run_area", fake_run_area)
    out = CliRunner().invoke(app, ["weekly-ingest", "--areas", str(_areas_file(tmp_path))])
    assert seen == [
        ("Credit River Mississauga", IngestSource.GLOBAL, 90),
        ("Bow River Calgary", IngestSource.GLOBAL, 90),
        ("Bow River Calgary", IngestSource.AB, 90),
    ]
    assert out.exit_code == 1
    assert "1 of 3 runs had a failed dataset" in out.output


def test_weekly_ingest_only_filters_by_label(tmp_path, monkeypatch):
    seen = []

    def fake_run_area(area, source, days_back=90):
        seen.append(area.label)
        return area_ingest.AreaIngestResult(label=area.label, source=source)

    monkeypatch.setattr(area_ingest, "run_area", fake_run_area)
    out = CliRunner().invoke(
        app, ["weekly-ingest", "--areas", str(_areas_file(tmp_path)), "--only", "credit"]
    )
    assert out.exit_code == 0, out.output
    assert seen == ["Credit River Mississauga"]
