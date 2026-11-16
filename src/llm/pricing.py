"""Estimated cost of one call.

An ESTIMATE for budgeting, not a bill: Anthropic's own usage dashboard is the
source of truth. Rates are $/1M tokens (input, output), matched by substring
against the model id (most specific first) because the id carries a date suffix
that changes across releases. Prompt-cache reads bill at 0.1x the input rate and
cache writes at 1.25x (5-minute TTL). A web search is billed per request.
"""

_RATES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("sonnet-5", (2.00, 10.00)),
    ("sonnet", (3.00, 15.00)),
    ("haiku", (1.00, 5.00)),
    ("opus", (5.00, 25.00)),
)

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25
WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches


def rates_for(model: str) -> tuple[float, float] | None:
    name = (model or "").lower()
    for needle, rates in _RATES:
        if needle in name:
            return rates
    return None


def estimate_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    web_searches: int = 0,
) -> float | None:
    """None when the model has no known rate: an unpriced call must not read as free."""
    rates = rates_for(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    usd = (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_creation_tokens * in_rate * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
    ) / 1_000_000
    return usd + web_searches * WEB_SEARCH_USD
