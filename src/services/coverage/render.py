"""Turn a coverage report into JSON and markdown.

The markdown leads with the gates, then the ladder tally per class, then the
misses grouped by cause: a report of misses reads as a work list because each
cause has one remedy.
"""

from pathlib import Path

from src.models.coverage import Cause, ClassReport, CoverageReport

LIST_LIMIT = 20

REMEDY = {
    Cause.MISSING_CURATION: "Add the water to the curation file (a product choice).",
    Cause.MISSING_DATA: "Fix the ingest or add a source; the stretch has no anchor of this type.",
    Cause.DETECTION: "Fix the spot builder; the anchor exists but no spot was made.",
    Cause.PLACEMENT: "Fix the geometry rule or the curated coordinates.",
    Cause.DEPLOY: "Fix the reseed; the spot is built but not on the host.",
    Cause.FILTER: "Change the demo tier or clamp rule.",
    Cause.RENDERING: "Fix the frontend (needs a browser to check).",
}


def to_json(report: CoverageReport) -> str:
    return report.model_dump_json(indent=2)


def _status(passed: bool, skipped: bool) -> str:
    if skipped:
        return "SKIPPED"
    return "pass" if passed else "FAIL"


def to_markdown(report: CoverageReport) -> str:
    lines = [
        f"# Coverage check: {report.jurisdiction}, focus `{report.focus}`",
        "",
        f"Generated {report.generated_at} from `{report.db_path}`.",
        "",
        "## Gates",
        "",
        "| Gate | Kind | Result | Measured | Threshold |",
        "|---|---|---|---|---|",
    ]
    for g in report.gates:
        lines.append(
            f"| {g.name} | {g.kind} | {_status(g.passed, g.skipped)} "
            f"| {g.measured} | {g.threshold} |"
        )
    lines.append("")
    verdict = (
        "**Hard gates failed. Do not ship this region or demo.**"
        if report.hard_failures
        else "No hard gate failed."
    )
    lines += [verdict, ""]

    for c in report.classes:
        lines += _class_section(c)

    if report.parity is not None:
        lines += [
            "## Deploy parity",
            "",
            f"Hosted counts from `{report.parity.source}`.",
            "",
            "| Table | Build | Host | |",
            "|---|---|---|---|",
        ]
        for r in report.parity.rows:
            build = "absent" if r.build_rows is None else str(r.build_rows)
            host = "absent" if r.hosted_rows is None else str(r.hosted_rows)
            lines.append(f"| {r.table} | {build} | {host} | {'ok' if r.ok else 'FAIL'} |")
        lines.append("")

    if report.liveness is not None:
        lv = report.liveness
        lines += [
            "## Liveness",
            "",
            f"`{lv.url}` via {lv.prober}: {lv.ok_count} of {len(lv.vantage_points)} ok "
            f"({lv.required} required).",
            "",
        ]
        for v in lv.vantage_points:
            lines.append(
                f"- {v.node}: {'ok' if v.ok else 'FAILED'} {v.status or ''} {v.detail}".rstrip()
            )
        lines.append("")

    if report.notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in report.notes] + [""]
    return "\n".join(lines)


def _class_section(c: ClassReport) -> list[str]:
    out = [f"## {c.feature_class}: {c.label}", ""]
    if c.unmeasured_reason:
        return out + [f"Could not be measured: {c.unmeasured_reason}", ""]
    recall = "n/a" if c.recall is None else f"{c.recall:.0%}"
    out += [
        f"- Reference features in the focus area: {c.reference_total}",
        f"- Filtered out as not near curated water: {c.out_of_scope}",
        f"- In scope: {c.in_scope}; on the map: {c.matched} (recall {recall})",
        "",
    ]
    for cause in Cause:
        n = c.by_cause.get(cause.value, 0)
        if not n or cause is Cause.MATCHED:
            continue
        out += [f"### {cause.value} ({n}): {REMEDY[cause]}", ""]
        shown = [r for r in c.results if r.cause is cause]
        for r in shown[:LIST_LIMIT]:
            out.append(f"- {r.name or r.ref_id} ({r.lat:.5f}, {r.lon:.5f}): {r.detail}")
        if len(shown) > LIST_LIMIT:
            out.append(f"- ... and {len(shown) - LIST_LIMIT} more (all of them are in the JSON)")
        out.append("")
    return out


def write_reports(report: CoverageReport, out_dir: Path) -> tuple[Path, Path]:
    """Write `<jurisdiction>_<focus>.json` and `.md` side by side. Returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"coverage_{report.jurisdiction.lower().replace('-', '_')}_{report.focus}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(to_json(report))
    md_path.write_text(to_markdown(report))
    return json_path, md_path
