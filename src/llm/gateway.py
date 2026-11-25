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


# Roles whose requests repeat a large stable prefix (system prompt, tool list).
_CACHED_ROLES = frozenset({"chat", "coaching"})
_BREAKPOINT = {"type": "ephemeral"}


def _with_cache_breakpoints(role: str, request: dict[str, Any]) -> dict[str, Any]:
    """Mark the stable prefix of a request for prompt caching.

    The API caches everything up to a breakpoint, in the order tools, system,
    messages. So the last tool gets a breakpoint, and so does the stable system
    block. A `system` string is all stable. A `system` list is split by the
    caller: its first block is the stable part and the rest is per-user text
    that follows the breakpoint. A caller that already set `cache_control` on a
    system block keeps its own placement. Other roles pass through unchanged.
    """
    if role not in _CACHED_ROLES:
        return request
    out = dict(request)
    tools = out.get("tools")
    if tools:
        out["tools"] = [*tools[:-1], {**tools[-1], "cache_control": _BREAKPOINT}]
    system = out.get("system")
    if isinstance(system, str) and system:
        out["system"] = [{"type": "text", "text": system, "cache_control": _BREAKPOINT}]
    elif isinstance(system, list) and system:
        if not any(isinstance(b, dict) and "cache_control" in b for b in system):
            out["system"] = [{**system[0], "cache_control": _BREAKPOINT}, *system[1:]]
    return out


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
        response = client.messages.create(model=model, **_with_cache_breakpoints(role, request))
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

    On normal exit, get_final_message() finishes consuming the stream and supplies
    usage. An exception before that completes records an error with no tokens.
    """
    model = model_for(role)
    client = client_mod.get_client()
    started = time.monotonic()
    try:
        with client.messages.stream(
            model=model, **_with_cache_breakpoints(role, request)
        ) as sdk_stream:
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
