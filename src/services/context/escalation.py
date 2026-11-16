"""Escalation for the records lookup — and only the records lookup.

    cached local records (iNat, GBIF, user catches)
           │ empty
           v
    live web search  ──────>  tagged `web`, unverified
           │ empty
           v
    honest empty  ─────────>  with a specific empty_reason

The chain runs in Python, not as a sequence of model tool choices. That
collapses three round trips into one retrieval pass, which is both the
clarity win and the single biggest cost lever.

Nothing else escalates. You would never web-search for pH or a barrier count
— those exist in the corpus or they do not, and a search result claiming
otherwise is worse than an honest gap.
"""

import logging
from urllib.parse import urlparse

from src import llm

logger = logging.getLogger(__name__)

# Sources the project will not consume, per its ethics rules: ToS violations
# and active enforcement. A general web search surfaces these constantly, so
# the filter has to live in code — a prompt asking the model to avoid them is
# exactly the kind of prose guardrail this architecture exists to replace.
BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "instagram.com",
        "facebook.com",
        "fb.com",
        "tiktok.com",
        "fishbrain.com",
        "fishangler.com",
    }
)


def is_blocked(url: str) -> bool:
    """True if the URL belongs to a forbidden source.

    Matches the registered domain and any subdomain, so
    `www.fishbrain.com` and `m.facebook.com` are both caught, while
    `notfishbrain.com` is not.
    """
    if not url:
        return True
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return True
    if not host:
        return True
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def filter_web_results(results: list[dict]) -> list[dict]:
    """Drop results from blocked sources. Logs what it dropped."""
    kept, dropped = [], 0
    for r in results:
        if is_blocked(str(r.get("url", ""))):
            dropped += 1
            continue
        kept.append(r)
    if dropped:
        logger.info("Dropped %d web result(s) from blocked sources", dropped)
    return kept


# -- the middle rung -----------------------------------------------------------
#
# Everything below turns the ladder in this module's docstring into working
# code. It was a diagram and a blocklist until now: `RecordsSlice` carried an
# `escalated_to_web` flag that nothing ever set, so a place with no local
# records returned an honest empty without ever trying the rung in between.

_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

_EXTRACTION_PROMPT = """Search the web for records of fish species observed at or near \
{place}{species_clause}.

Then return ONLY a JSON array, no prose before or after. Each element:

  {{"species": "...", "url": "...", "source": "...", "date": "YYYY-MM-DD or null"}}

Hard rules:
- Every entry MUST have a URL you actually retrieved. No URL, no entry.
- Only list a species if the page states it was observed, caught, surveyed or
  stocked at this water. Do not list species you believe are probably present.
- Do not infer from range maps or from what is typical for the region.
- If nothing qualifies, return exactly: []
"""


def escalate_records(
    place_name: str,
    lat: float,
    lng: float,
    species_filter: str | None = None,
    *,
    user_id: int | None,
    max_results: int = 10,
):
    """The rung between the corpus and an honest empty.

    Returns `(records, empty_reason)`. Records carry WEB provenance, which the
    `Provenance` validator forces to `verified=False` — they can never render
    as peers of an ingested observation no matter what a caller does with them.

    Only ever called when the local lookup came back empty. Nothing else in the
    layer escalates: you would never web-search for pH or a barrier count, and
    a search result asserting one is worse than an honest gap.
    """
    from src.models.context import EmptyReason, Provenance, ProvenanceKind, SpeciesRecord

    where = place_name or f"{lat:.4f}, {lng:.4f}"
    species_clause = f", specifically {species_filter}" if species_filter else ""
    prompt = _EXTRACTION_PROMPT.format(place=where, species_clause=species_clause)

    try:
        resp = llm.complete(
            "web_escalation",
            caller="escalation.escalate_records",
            user_id=user_id,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            tools=[_SEARCH_TOOL],
        )
    except Exception:  # noqa: BLE001 - an outage is a transient empty, not a gap
        logger.warning("web escalation failed for %s", where, exc_info=True)
        return [], EmptyReason.LIVE_LOOKUP_FAILED

    raw = _parse_entries(_response_text(resp))
    if raw is None:
        logger.warning("web escalation returned unparseable output for %s", where)
        return [], EmptyReason.LIVE_LOOKUP_FAILED

    # A URL is not decoration here. It is the only thing that makes a WEB claim
    # checkable, and an entry without one is indistinguishable from the model
    # having made it up — which is the exact failure this layer exists to stop.
    entries = [e for e in raw if isinstance(e, dict) and str(e.get("url", "")).strip()]
    entries = filter_web_results(entries)

    records = []
    seen: set[str] = set()
    for e in entries[:max_results]:
        species = str(e.get("species") or "").strip()
        if not species or species.lower() in seen:
            continue
        seen.add(species.lower())
        date = e.get("date")
        date = str(date) if date else None
        records.append(
            SpeciesRecord(
                species=species,
                most_recent=date,
                provenance=Provenance(
                    kind=ProvenanceKind.WEB,
                    source=str(e.get("source") or "web search"),
                    url=str(e["url"]),
                    date=date,
                ),
            )
        )

    if not records:
        return [], EmptyReason.WEB_SEARCH_EMPTY
    return records, None


def _response_text(resp) -> str:
    """Concatenate the text blocks, ignoring the server tool's own blocks."""
    out = []
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "text":
            out.append(getattr(block, "text", "") or "")
    return "".join(out).strip()


def _parse_entries(text: str):
    """Pull the JSON array out of the reply.

    Returns None on anything unparseable rather than an empty list: "the model
    produced garbage" and "the web has nothing" are different facts with
    different remedies, and collapsing them would report an outage as absence.
    """
    import json

    if not text:
        return None
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None
