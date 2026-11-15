"""The listing-table invariant: the protection gate must have data to refuse with.

When the table that holds conservation status has no rows for a jurisdiction, every
species reads as "status unknown" and a gate that treats unknown as safe withholds
nothing. That is how an endangered fish stayed on cards. The invariant fails on
the empty table, before any card is looked at.
"""

from src.models.coverage import DbProbe, ListingReport


def check_listing(
    probe: DbProbe, jurisdiction: str, leak_count: int | None = None
) -> ListingReport:
    return ListingReport(
        table=probe.listing.table,
        jurisdiction=jurisdiction,
        rows_for_jurisdiction=probe.listing.rows_for_jurisdiction,
        rows_total=probe.listing.rows_total,
        table_present=probe.listing.table_present,
        leak_count=leak_count,
    )
