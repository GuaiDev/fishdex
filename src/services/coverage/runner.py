"""Run the coverage check for one jurisdiction and focus area and decide the gates.

Two modes, as the report specifies (1.6):

- build mode (`db_path` alone): rungs 1-3 of the ladder and the listing invariant,
  against one database file. Run it before every reseed.
- host mode (`hosted_db` or `api_url`): adds deploy parity, the deploy rung, and with
  an API URL, liveness. Run it after every reseed and before any demo.

Hard gates block shipping a region or a demo; focus gates are the region's quality
bar and inform. The exit code follows the hard gates only.
"""

import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.models.coverage import (
    ClassReport,
    CoverageRegistry,
    CoverageReport,
    DbProbe,
    GateResult,
    ListingReport,
    LivenessReport,
    ParityReport,
)
from src.services.coverage import liveness as liveness_mod
from src.services.coverage.host import fetch_host_probe
from src.services.coverage.ladder import check_point_class
from src.services.coverage.listing import check_listing
from src.services.coverage.parity import compare_parity
from src.services.coverage.probe import open_readonly, probe_connection

logger = logging.getLogger(__name__)


def run_coverage_check(
    registry: CoverageRegistry,
    focus_name: str,
    db_path: Path,
    *,
    hosted_db: Path | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    fetch=None,
    probe_outside=None,
    host_probe=None,
    now: datetime | None = None,
) -> CoverageReport:
    """Run every check the mode allows. `fetch`, `probe_outside` and `host_probe` are seams
    for tests; production leaves them at the real adapters."""
    if focus_name not in registry.focus:
        raise ValueError(
            f"unknown focus {focus_name!r} for {registry.jurisdiction}; "
            f"the registry defines: {', '.join(sorted(registry.focus))}"
        )
    if fetch is None:
        fetch = importlib.import_module("src.ingest.global.arcgis_reference").fetch_reference
    probe_outside = probe_outside or liveness_mod.probe_outside
    host_probe = host_probe or fetch_host_probe

    focus = registry.focus[focus_name]
    jurisdiction = registry.jurisdiction
    all_kinds = sorted({k for ref in registry.references for k in ref.spot_kinds})
    notes: list[str] = []

    conn = open_readonly(db_path)
    try:
        build = probe_connection(
            conn,
            source=str(db_path),
            tables=registry.parity.tables,
            listing_table=registry.listing.table,
            jurisdiction=jurisdiction,
            spot_kinds=all_kinds,
            bbox=None,
        )

        hosted: DbProbe | None = None
        if hosted_db is not None:
            hconn = open_readonly(hosted_db)
            try:
                hosted = probe_connection(
                    hconn,
                    source=str(hosted_db),
                    tables=registry.parity.tables,
                    listing_table=registry.listing.table,
                    jurisdiction=jurisdiction,
                    spot_kinds=all_kinds,
                    bbox=None,
                )
            finally:
                hconn.close()
        elif api_url:
            if not api_key:
                notes.append(
                    "Deploy parity and the deploy rung were skipped: `--api` needs an admin key "
                    "(`--api-key` or FISHBOT_API_KEY) to read the host's tables."
                )
            else:
                hosted = host_probe(
                    api_url,
                    api_key,
                    tables=registry.parity.tables,
                    listing_table=registry.listing.table,
                    jurisdiction=jurisdiction,
                    spot_kinds=all_kinds,
                    bbox=None,
                )

        classes: list[ClassReport] = []
        for spec in registry.references:
            fetched = fetch(spec, focus.bbox)
            if fetched.skipped:
                notes.append(
                    f"{spec.label}: {fetched.skipped} of {fetched.raw_count} reference features "
                    f"could not be read and are not counted ({fetched.skipped_reasons})."
                )
            if not spec.independent:
                notes.append(
                    f"{spec.label} is also a map source, so recall against it measures "
                    "self-agreement, not coverage."
                )
            report = check_point_class(spec, fetched.features, conn, jurisdiction, hosted=hosted)
            classes.append(report)
    finally:
        conn.close()

    parity = compare_parity(build, hosted, registry.parity.tables) if hosted else None
    # The gate protects the host's users, so judge the host's table when we can see it.
    listing_probe = hosted if hosted is not None else build
    listing = check_listing(listing_probe, jurisdiction)
    if listing.leak_count is None:
        notes.append(
            "Listed-species leak count was not measured: it needs the cards, which this check "
            "does not fetch. The invariant here is that status data exists to refuse with."
        )

    live: LivenessReport | None = None
    if api_url:
        live = probe_outside(api_url, registry.liveness.path, registry.liveness.min_vantage_points)

    gates = _decide_gates(
        registry, classes, parity, listing, live, api_url, hosted_given=hosted is not None
    )
    return CoverageReport(
        jurisdiction=jurisdiction,
        focus=focus_name,
        db_path=str(db_path),
        generated_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        classes=classes,
        parity=parity,
        listing=listing,
        liveness=live,
        gates=gates,
        notes=notes,
    )


def _decide_gates(
    registry: CoverageRegistry,
    classes: list[ClassReport],
    parity: ParityReport | None,
    listing: ListingReport,
    live: LivenessReport | None,
    api_url: str | None,
    *,
    hosted_given: bool,
) -> list[GateResult]:
    gates: list[GateResult] = []

    # Hard: reachable from outside.
    if live is not None:
        gates.append(
            GateResult(
                name="api_reachable",
                kind="hard",
                passed=live.ok,
                measured=(
                    f"{live.ok_count} of {len(live.vantage_points)} vantage points "
                    f"returned 200 (via {live.prober})"
                ),
                threshold=f"{live.required} of {live.required} outside vantage points return 200",
            )
        )
    else:
        gates.append(
            GateResult(
                name="api_reachable",
                kind="hard",
                passed=False,
                skipped=True,
                measured="not run: needs --api",
                threshold=(
                    f"{registry.liveness.min_vantage_points} outside vantage points return 200"
                ),
            )
        )

    # Hard: deploy parity.
    if parity is not None:
        bad = parity.failures
        gates.append(
            GateResult(
                name="deploy_parity",
                kind="hard",
                passed=not bad,
                measured=(
                    "every map table matches"
                    if not bad
                    else "; ".join(f"{r.table} {r.hosted_rows or 0} of {r.build_rows}" for r in bad)
                ),
                threshold="100% of build rows on the host, for every map table",
            )
        )
    else:
        gates.append(
            GateResult(
                name="deploy_parity",
                kind="hard",
                passed=False,
                skipped=True,
                measured="not run: needs --hosted-db or --api with an admin key",
                threshold="100% of build rows on the host, for every map table",
            )
        )

    # Hard: the listing table.
    side = "host" if hosted_given else "build"
    gates.append(
        GateResult(
            name="listing_table",
            kind="hard",
            passed=listing.ok,
            measured=(
                f"{listing.table} on the {side}: "
                + (
                    f"{listing.rows_for_jurisdiction} rows for {listing.jurisdiction}"
                    if listing.table_present
                    else "table missing"
                )
                + (
                    ", leak count not measured"
                    if listing.leak_count is None
                    else f", leak count {listing.leak_count}"
                )
            ),
            threshold=f"more than 0 rows for {listing.jurisdiction}, and leak count 0",
        )
    )

    # Focus: per-class in-scope recall.
    for c in classes:
        if c.min_recall is None:
            continue
        if c.unmeasured_reason:
            gates.append(
                GateResult(
                    name=f"{c.feature_class}_in_scope_recall",
                    kind="focus",
                    passed=False,
                    measured=f"could not be measured: {c.unmeasured_reason}",
                    threshold=f">= {c.min_recall:.0%}",
                )
            )
            continue
        if c.recall is None:
            gates.append(
                GateResult(
                    name=f"{c.feature_class}_in_scope_recall",
                    kind="focus",
                    passed=True,
                    measured=f"0 in scope of {c.reference_total} reference features",
                    threshold=f">= {c.min_recall:.0%}",
                )
            )
            continue
        gates.append(
            GateResult(
                name=f"{c.feature_class}_in_scope_recall",
                kind="focus",
                passed=bool(c.passes),
                measured=f"{c.matched} of {c.in_scope} ({c.recall:.0%})",
                threshold=f">= {c.min_recall:.0%}",
            )
        )
    return gates
