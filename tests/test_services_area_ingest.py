"""The weekly ingest: area list as data, run through the service, failures counted."""

import json

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.services import area_ingest
from src.services.area_ingest import load_areas


def test_plugin_area_list_matches_the_old_area_file():
    """The 25 areas the retired data/ingest_areas.json listed now come from the plugins.

    Coordinates, radii and per-source radii are unchanged. Province bundles became
    their named sources (bc -> hydro_network, fish_observations, water_quality ...).
    """
    areas = load_areas()
    assert len(areas) == 25
    assert len({a.label for a in areas}) == 25
    by_label = {a.label: a for a in areas}

    bronte = by_label["Bronte Creek Oakville"]
    assert (bronte.jurisdiction, bronte.lat, bronte.lng, bronte.radius_km) == (
        "CA-ON",
        43.45,
        -79.72,
        25,
    )
    assert bronte.sources == ["global"]
    assert by_label["Miramichi River NB"].radius_for("tidal") == 100
    assert by_label["Miramichi River NB"].sources == ["global", "tidal"]
    assert by_label["Skeena River Terrace"].sources == [
        "global",
        "hydro_network",
        "fish_observations",
        "water_quality",
    ]
    for label in (
        "Bow River Calgary",
        "North Saskatchewan River Edmonton",
        "Oldman River Lethbridge",
    ):
        assert "stocking" in by_label[label].sources
        assert by_label[label].jurisdiction == "CA-AB"
    assert "hydro_network" in by_label["Red River Winnipeg"].sources
    assert "fish_observations" in by_label["South Saskatchewan River Saskatoon"].sources


def test_every_scheduled_job_is_runnable():
    """A typo in a plugin's area sources would otherwise surface halfway through a weekly run."""
    for a in load_areas():
        for s in a.sources:
            area_ingest.check_job(a.jurisdiction, s)


def test_load_areas_rejects_a_malformed_area(tmp_path):
    p = tmp_path / "areas.json"
    p.write_text(json.dumps({"areas": [{"label": "x", "lat": 1, "lng": 2}]}))
    with pytest.raises(ValueError):
        load_areas(p)


def test_attempt_records_counts_and_failures_without_stopping():
    r = area_ingest.AreaIngestResult(label="x", source="global")
    area_ingest._attempt(r, "first", lambda: 5)
    area_ingest._attempt(r, "flaky", lambda: 1 / 0)
    area_ingest._attempt(r, "bundle", lambda: {"a": 1, "b": 2})
    assert r.stored == {"first": 5, "a": 1, "b": 2}
    assert r.failed == {"flaky": "ZeroDivisionError: division by zero"}
    assert not r.ok


def test_run_area_dispatches_with_the_per_source_radius(monkeypatch):
    calls = []

    def fake_global(lat, lng, radius_km, label, days_back):
        calls.append(("global", lat, lng, radius_km, label, days_back))
        return area_ingest.AreaIngestResult(label=label, source="global")

    class _Runner:
        def __call__(self, ctx):
            calls.append(("tidal", ctx.lat, ctx.lng, ctx.radius_km, ctx.label))
            return 7

    monkeypatch.setattr(area_ingest, "run_global_ingest", fake_global)
    monkeypatch.setattr(area_ingest, "ensure_schema", lambda db: None)
    monkeypatch.setattr(area_ingest, "get_db", lambda: None)
    monkeypatch.setattr(area_ingest.SourceSpec, "load_runner", lambda self: _Runner())
    area = area_ingest.IngestArea(
        label="Annapolis River NS",
        jurisdiction="CA-NS",
        lat=44.98,
        lng=-65.5,
        radius_km=40,
        sources=["global", "tidal"],
        source_radius_km={"tidal": 100},
    )
    results = [area_ingest.run_area(area, s, days_back=90) for s in area.sources]
    assert calls == [
        ("global", 44.98, -65.5, 40, "Annapolis River NS", 90),
        ("tidal", 44.98, -65.5, 100, "Annapolis River NS"),
    ]
    assert results[1].stored == {"CHS tide predictions": 7}


@pytest.mark.parametrize("radius, expected", [(None, 100), (20, 20)])
def test_ingest_source_configured_area_radius_reaches_runner(monkeypatch, radius, expected):
    calls = []
    configured = area_ingest.load_areas()
    monkeypatch.setattr(area_ingest, "load_areas", lambda: configured)
    monkeypatch.setattr(area_ingest, "ensure_schema", lambda db: None)
    monkeypatch.setattr(area_ingest, "get_db", lambda: None)
    monkeypatch.setattr(
        area_ingest.SourceSpec,
        "load_runner",
        lambda self: lambda ctx: calls.append(ctx.radius_km) or 7,
    )
    args = ["ingest-source", "CA-NS", "tidal", "--area", "Annapolis River NS"]
    if radius is not None:
        args.extend(["--radius", str(radius)])

    out = CliRunner().invoke(app, args)

    assert out.exit_code == 0, out.output
    assert calls == [expected]
    original = next(a for a in configured if a.label == "Annapolis River NS")
    assert original.radius_for("tidal") == 100


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
                        "sources": ["global", "stocking"],
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
        if source == "stocking":
            r.failed["AB stocking"] = "HTTPError: 503"
        return r

    monkeypatch.setattr(area_ingest, "run_area", fake_run_area)
    out = CliRunner().invoke(app, ["weekly-ingest", "--areas", str(_areas_file(tmp_path))])
    assert seen == [
        ("Credit River Mississauga", "global", 90),
        ("Bow River Calgary", "global", 90),
        ("Bow River Calgary", "stocking", 90),
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
