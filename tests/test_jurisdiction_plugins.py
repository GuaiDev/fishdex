"""One jurisdiction registry: every region is a folder with a `plugin.py` manifest."""

import importlib
import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

import src.ingest.jurisdictions as jurisdiction_pkg
from src.cli.main import app
from src.jurisdictions.geo import jurisdiction_for_coords
from src.jurisdictions.registry import (
    GLOBAL_SOURCE,
    all_plugins,
    get_jurisdiction,
    get_plugin,
    reload_plugins,
)
from src.services import area_ingest
from src.services.area_ingest import IngestJobError, check_job, load_areas

FIXTURE = Path(__file__).parent / "fixtures" / "jurisdiction_baseline.json"


# ── Ontario (and everywhere else) behaves as it did before the merge ──────────


def test_coordinate_tagging_matches_the_pre_registry_boxes():
    """2,310 grid points tagged by the old hand-ordered box list, captured before the merge."""
    baseline = json.loads(FIXTURE.read_text())["points"]
    wrong = {
        k: (want, jurisdiction_for_coords(*map(float, k.split(","))))
        for k, want in baseline.items()
        if jurisdiction_for_coords(*map(float, k.split(","))) != want
    }
    assert wrong == {}


def test_ontario_prompt_text_is_unchanged():
    want = json.loads(FIXTURE.read_text())["ontario_context"]
    j = get_jurisdiction("CA-ON")
    assert j.regulatory_context() == want
    assert (j.code, j.name, j.country, j.has_detailed_data) == ("CA-ON", "Ontario", "CA", True)


def test_ontario_weekly_areas_are_the_four_global_only_areas():
    areas = [a for a in load_areas() if a.jurisdiction == "CA-ON"]
    assert [a.label for a in areas] == [
        "Grand River Dunnville",
        "Credit River Mississauga",
        "Bronte Creek Oakville",
        "Thames River London",
    ]
    assert all(a.sources == ["global"] for a in areas)


# ── the manifests are internally true ─────────────────────────────────────────


def test_each_plugin_code_matches_its_folder_and_is_unique():
    folders = {
        p.name for p in Path(jurisdiction_pkg.__path__[0]).iterdir() if (p / "plugin.py").exists()
    }
    codes = {p.code for p in all_plugins()}
    assert {c.lower().replace("-", "_") for c in codes} == folders


def test_bbox_priorities_are_unique():
    priorities = [p.bbox.priority for p in all_plugins() if p.bbox]
    assert len(priorities) == len(set(priorities))


def _sources():
    return [(p.code, n, s) for p in all_plugins() for n, s in p.sources.items()]


@pytest.mark.parametrize(
    "code,name,spec", _sources(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_declared_runners_resolve_to_callables(code, name, spec):
    if spec.runner:
        assert callable(spec.load_runner()), f"{code} {name}"


@pytest.mark.parametrize(
    "code,name,spec", _sources(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_declared_ttl_matches_the_adapter_constant(code, name, spec):
    if not spec.ttl_constant:
        return
    module, _, const = spec.ttl_constant.partition(":")
    seconds = getattr(importlib.import_module(module), const)
    assert spec.ttl_days == seconds / 86400, f"{code} {name}: manifest drifted from {const}"


def test_declared_adapter_modules_exist():
    for p in all_plugins():
        for module in (p.hydro_adapter, p.regulations_adapter):
            if module:
                assert importlib.util.find_spec(module) is not None, f"{p.code}: {module}"


def test_declared_curation_files_exist():
    for p in all_plugins():
        for role, path in p.curation.items():
            assert Path(path).exists(), f"{p.code} {role}: {path}"


def test_a_source_with_no_runner_is_refused_with_the_reason():
    with pytest.raises(IngestJobError, match="no runner"):
        check_job("CA-ON", "hydro_network")
    with pytest.raises(IngestJobError, match="has no source 'nope'.*can run: global"):
        check_job("CA-ON", "nope")
    with pytest.raises(IngestJobError, match="unknown jurisdiction"):
        check_job("XX-YY", "hydro_network")
    assert check_job("XX-YY", GLOBAL_SOURCE) is None


def test_quiet_water_reads_the_plugin_declared_exclusion_file():
    from src.services.quiet_water import load_exclusions

    assert load_exclusions("CA-ON"), "Ontario's First Nations exclusion zones must still load"
    assert load_exclusions("CA-BC") == []


# ── adding a jurisdiction touches only its own folder ─────────────────────────


@pytest.fixture
def stub_region(tmp_path, monkeypatch):
    """A made-up region ZZ-QQ, written as nothing but its own folder."""
    root = tmp_path / "extra"
    folder = root / "zz_qq"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    (folder / "ingest.py").write_text(
        textwrap.dedent(
            """
            CALLS = []

            def run_widgets(ctx):
                CALLS.append((ctx.label, ctx.lat, ctx.lng, ctx.radius_km))
                return 3
            """
        )
    )
    (folder / "plugin.py").write_text(
        textwrap.dedent(
            """
            from src.jurisdictions.plugin import BBox, CronArea, JurisdictionPlugin, SourceSpec

            PLUGIN = JurisdictionPlugin(
                code="ZZ-QQ",
                name="Qqland",
                country="ZZ",
                bbox=BBox(-10.0, -5.0, 100.0, 105.0, priority=999),
                regulatory_context="## Qqland (ZZ-QQ)\\n\\nBring a licence.",
                sources={
                    "widgets": SourceSpec(
                        status="implemented",
                        runner="src.ingest.jurisdictions.zz_qq.ingest:run_widgets",
                        label="Qq widgets",
                    ),
                    "ranges": SourceSpec(status="planned"),
                },
                cron_areas=(
                    CronArea("Test Water", -7.5, 102.5, 20.0, sources=("global", "widgets")),
                ),
                status_authorities=("Qqland Fish Act",),
            )
            """
        )
    )
    monkeypatch.setattr(jurisdiction_pkg, "__path__", [*jurisdiction_pkg.__path__, str(root)])
    reload_plugins()
    yield folder
    for name in [m for m in sys.modules if m.startswith("src.ingest.jurisdictions.zz_qq")]:
        del sys.modules[name]
    monkeypatch.undo()
    reload_plugins()


def test_a_new_folder_is_the_whole_change(stub_region, monkeypatch):
    # registered, tagged, prompted, scheduled
    assert get_plugin("ZZ-QQ").name == "Qqland"
    assert jurisdiction_for_coords(-7.5, 102.5) == "ZZ-QQ"
    assert "Bring a licence." in get_jurisdiction("ZZ-QQ").regulatory_context()
    assert [
        (a.label, a.jurisdiction, a.sources) for a in load_areas() if a.jurisdiction == "ZZ-QQ"
    ] == [("Test Water", "ZZ-QQ", ["global", "widgets"])]
    # Ontario is untouched by the addition
    assert jurisdiction_for_coords(43.45, -79.72) == "CA-ON"

    # runnable through the one generic command, with no other file edited
    monkeypatch.setattr(area_ingest, "ensure_schema", lambda db: None)
    monkeypatch.setattr(area_ingest, "get_db", lambda: None)
    out = CliRunner().invoke(app, ["ingest-source", "ZZ-QQ", "widgets", "--area", "test water"])
    assert out.exit_code == 0, out.output
    assert "Qq widgets: 3" in out.output
    calls = importlib.import_module("src.ingest.jurisdictions.zz_qq.ingest").CALLS
    assert calls == [("Test Water", -7.5, 102.5, 20.0)]

    # declared-but-unbuilt sources are refused, not silently skipped
    out = CliRunner().invoke(app, ["ingest-source", "ZZ-QQ", "ranges", "--lat", "1", "--lng", "2"])
    assert out.exit_code == 1 and "no runner" in out.output


def test_a_plugin_filed_under_the_wrong_code_is_an_error(tmp_path, monkeypatch):
    folder = tmp_path / "extra" / "zz_ww"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    (folder / "plugin.py").write_text(
        "from src.jurisdictions.plugin import JurisdictionPlugin\n"
        'PLUGIN = JurisdictionPlugin(code="ZZ-XX", name="x", country="ZZ")\n'
    )
    monkeypatch.setattr(
        jurisdiction_pkg, "__path__", [*jurisdiction_pkg.__path__, str(tmp_path / "extra")]
    )
    reload_plugins()
    try:
        with pytest.raises(ValueError, match="expected 'ZZ-WW'"):
            all_plugins()
    finally:
        for name in [m for m in sys.modules if m.startswith("src.ingest.jurisdictions.zz_ww")]:
            del sys.modules[name]
        monkeypatch.undo()
        reload_plugins()


def test_ingest_source_needs_an_area_for_an_area_scoped_source():
    out = CliRunner().invoke(app, ["ingest-source", "CA-BC", "hydro_network"])
    assert out.exit_code == 1 and "--area" in out.output
    out = CliRunner().invoke(app, ["ingest-source", "CA-BC", "hydro_network", "--area", "Nowhere"])
    assert out.exit_code == 1 and "Fraser River Lower Mainland" in out.output


def test_weekly_ingest_dry_run_lists_every_plugin_area():
    out = CliRunner().invoke(app, ["weekly-ingest", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "weekly-ingest plan" in out.output
