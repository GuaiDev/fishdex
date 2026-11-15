"""Deploy parity: the host must hold every row of every map table the build produced."""

from src.models.coverage import DbProbe, ParityReport, ParityRow


def compare_parity(build: DbProbe, hosted: DbProbe, tables: list[str]) -> ParityReport:
    """One row per registry table, build count beside hosted count.

    A table the build has no rows of cannot fail (there is nothing to deploy). A
    table the host lacks, or holds fewer rows of, fails.
    """
    rows = [
        ParityRow(
            table=t,
            build_rows=build.table_rows.get(t),
            hosted_rows=hosted.table_rows.get(t),
        )
        for t in tables
    ]
    return ParityReport(source=hosted.source, rows=rows)
