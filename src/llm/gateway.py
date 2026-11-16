"""`complete` and `stream`: the only functions that call the Anthropic API."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.llm import client as client_mod
from src.llm import ledger
from src.llm.pricing import estimate_usd
from src.llm.registry import model_for

logger = logging.getLogger(__name__)


def _count(value: Any) -> int:
    """A token count, or 0 when the SDK object does not carry one (older SDKs, fakes)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage_row(
    *,
    response: Any,
    role: str,
    caller: str,
    user_id: int | None,
    session_id: str | None,
    fallback_model: str,
    latency_ms: int,
) -> ledger.UsageRow:
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", None)
    model = model if isinstance(model, str) and model else fallback_model
    input_tokens = _count(getattr(usage, "input_tokens", 0))
    output_tokens = _count(getattr(usage, "output_tokens", 0))
    cache_creation = _count(getattr(usage, "cache_creation_input_tokens", 0))
    cache_read = _count(getattr(usage, "cache_read_input_tokens", 0))
    server_tools = getattr(usage, "server_tool_use", None)
    searches = _count(getattr(server_tools, "web_search_requests", 0))
    return ledger.UsageRow(
        user_id=user_id,
        session_id=session_id,
        caller=caller,
        role=role,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        web_searches=searches,
        est_usd=estimate_usd(
            model, input_tokens, output_tokens, cache_creation, cache_read, searches
        ),
        latency_ms=latency_ms,
    )


def _failure_row(
    *,
    role: str,
    caller: str,
    user_id: int | None,
    session_id: str | None,
    model: str,
    latency_ms: int,
    exc: BaseException,
) -> ledger.UsageRow:
    return ledger.UsageRow(
        user_id=user_id,
        session_id=session_id,
        caller=caller,
        role=role,
        model=model,
        latency_ms=latency_ms,
        status="error",
        error=f"{type(exc).__name__}: {exc}"[:300],
    )


def complete(
    role: str,
    *,
    caller: str,
    user_id: int | None,
    session_id: str | None = None,
    **request: Any,
) -> Any:
    """Make one model call and write one ledger row.

    `role` picks the model; `caller` names the call site for the ledger;
    `user_id` is whose request this is, or None for work no user asked for (a
    batch job). Everything else is passed to `messages.create` unchanged.
    A failed call writes an `error` row and re-raises.
    """
    model = model_for(role)
    client = client_mod.get_client()
    started = time.monotonic()
    try:
        response = client.messages.create(model=model, **request)
    except BaseException as exc:
        ledger.record(
            _failure_row(
                role=role,
                caller=caller,
                user_id=user_id,
                session_id=session_id,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                exc=exc,
            )
        )
        raise
    ledger.record(
        _usage_row(
            response=response,
            role=role,
            caller=caller,
            user_id=user_id,
            session_id=session_id,
            fallback_model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    )
    return response


@contextmanager
def stream(
    role: str,
    *,
    caller: str,
    user_id: int | None,
    session_id: str | None = None,
    **request: Any,
) -> Iterator[Any]:
    """Streaming twin of `complete`: yields the SDK stream, writes the row on exit.

    The row is written from the final message, so a stream the caller abandons
    before it finishes is recorded as an error with no tokens.
    """
    model = model_for(role)
    client = client_mod.get_client()
    started = time.monotonic()
    try:
        with client.messages.stream(model=model, **request) as sdk_stream:
            yield sdk_stream
            final = sdk_stream.get_final_message()
    except BaseException as exc:
        ledger.record(
            _failure_row(
                role=role,
                caller=caller,
                user_id=user_id,
                session_id=session_id,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                exc=exc,
            )
        )
        raise
    ledger.record(
        _usage_row(
            response=final,
            role=role,
            caller=caller,
            user_id=user_id,
            session_id=session_id,
            fallback_model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    )
