"""Interactive chat loop using the Anthropic SDK + rich for terminal I/O."""

import logging
from datetime import datetime
from typing import Any

from anthropic import Anthropic, APIError
from rich.console import Console

from src.agent.client import get_client, get_model
from src.agent.system_prompt import assemble, load_template
from src.agent.tools import execute_tool, tool_schemas
from src.jurisdictions.registry import get_jurisdiction
from src.storage.angler_context import format_context_for_prompt, load_context
from src.storage.conversations import close_and_update_context, save_turn, start_session
from src.storage.database import get_db
from src.storage.profile import load_profile
from src.storage.trips import recent_trips

EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit"}

MAX_TURNS_BEFORE_SUMMARY = 10

HAIKU = "claude-haiku-4-5-20251001"

logger = logging.getLogger(__name__)


def _summarize_history(messages: list[dict], client) -> list[dict]:
    """Summarize the oldest half of conversation history once it exceeds MAX_TURNS_BEFORE_SUMMARY.

    Keeps the most recent 6 turns verbatim for immediate context.
    """
    if len(messages) < MAX_TURNS_BEFORE_SUMMARY * 2:
        return messages

    keep_recent = 12  # 6 turns = 12 messages
    to_summarize = messages[:-keep_recent]
    to_keep = messages[-keep_recent:]

    summary_prompt = (
        "Summarize this fishing conversation history into a compact context block. "
        "Keep: species discussed, locations mentioned, tactics covered, any confirmed "
        "patterns or insights, and any trip data logged. "
        "Discard: filler, greetings, anything redundant. "
        "Output as a brief bulleted list under the heading '## Conversation context'.\n\n"
        + "\n".join(
            f"{m['role'].upper()}: {m['content'][:500]}"
            for m in to_summarize
            if isinstance(m.get("content"), str)
        )
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": summary_prompt}],
    )

    summary_text = response.content[0].text

    return [
        {"role": "user", "content": summary_text},
        {
            "role": "assistant",
            "content": "Understood, I have the context from our earlier conversation.",
        },
    ] + to_keep


def _run_full_pipeline(
    messages: list[dict], session_id: str, mode: str = "synthesis", user_id: int = 1
) -> dict:
    """Non-streaming agentic loop. The synthesis path and fallback for all modes."""
    try:
        client = get_client()
    except RuntimeError as e:
        return {"reply": str(e), "tool_calls": [], "messages": messages}

    profile = load_profile()
    db = get_db()
    trips = recent_trips(db, limit=5)
    home = get_jurisdiction(profile.home_jurisdiction)
    system_prompt = assemble(load_template(), profile, trips, home)
    start_session(db, session_id)
    angler_context = load_context(db, user_id=user_id)
    if angler_context:
        system_prompt = system_prompt + "\n\n" + format_context_for_prompt(angler_context)
    if user_id != 1:
        system_prompt += (
            f"\n\nCurrent user: {user_id} (filter all personal data queries by this user_id)"
        )
    model = get_model()
    tools = _tools(profile)
    tool_calls_made: list[str] = []

    while True:
        messages = _summarize_history(messages, client)

        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )

        usage = resp.usage
        db["api_usage"].insert(
            {
                "session_id": session_id,
                "model": resp.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.input_tokens + usage.output_tokens,
                "tool_calls_made": len([b for b in resp.content if b.type == "tool_use"]),
                "endpoint": "chat",
            }
        )

        content_blocks = resp.content
        tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]

        if not tool_use_blocks:
            reply = "".join(b.text for b in content_blocks if b.type == "text")
            messages.append({"role": "assistant", "content": reply})

            # Save the last user + assistant turn
            user_msgs = [
                m for m in messages if m["role"] == "user" and isinstance(m.get("content"), str)
            ]
            if user_msgs:
                save_turn(db, session_id, "user", user_msgs[-1]["content"], len(user_msgs) - 1)
            save_turn(db, session_id, "assistant", reply, len(user_msgs))

            # Lazy context update after 15 turns
            session_row = db["chat_sessions"].get(session_id)
            if session_row and (session_row.get("turn_count") or 0) >= 15:
                if not session_row.get("summary"):
                    close_and_update_context(db, session_id, messages, client, user_id=user_id)

            return {"reply": reply, "tool_calls": tool_calls_made, "messages": messages}

        assistant_content = [_normalize_block(b) for b in content_blocks]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        for block in tool_use_blocks:
            tool_calls_made.append(block.name)
            result = _execute_tool(block.name, block.input, user_id=user_id)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            try:
                import json as _json

                db["tool_usage"].insert(
                    {
                        "session_id": session_id,
                        "tool_name": block.name,
                        "input_summary": str(_json.dumps(block.input))[:200],
                        "success": 1,
                    }
                )
            except Exception:
                pass
        messages.append({"role": "user", "content": tool_results})


def run_chat_api(
    messages: list[dict],
    session_id: str | None = None,
    routing_enabled: bool = True,
    user_id: int = 1,
) -> dict:
    """Routed entry point. Classifies the latest user message and dispatches to the
    cheapest capable path. Falls back to the full pipeline on synthesis or any uncertainty.
    """
    if session_id is None:
        session_id = datetime.now().isoformat()

    # Extract the latest user message text
    latest_user = None
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m.get("content"), str):
            latest_user = m["content"]
            break

    if not routing_enabled or latest_user is None:
        return _run_full_pipeline(messages, session_id, user_id=user_id)

    from src.agent.router import classify_message, handle_reflex

    recent = " ".join(
        m["content"][:200] for m in messages[-4:] if isinstance(m.get("content"), str)
    )

    classification = classify_message(latest_user, recent)
    mode = classification["mode"]

    # The reflex path answers from general knowledge with no retrieval and no
    # tools. That is right for "how do I tie a palomar knot" and catastrophic
    # for "does Bronte Creek hold brook trout" — the second gets invented.
    # Until now the only thing standing between those two was a sentence in
    # the classifier's system prompt asking it to notice place names, which is
    # a prose guardrail on the highest-stakes property the product has.
    # Deciding it in Python is the same move as putting escalation there.
    if mode == "reflex":
        named_place = _reflex_names_a_place(latest_user, user_id)
        if named_place:
            logger.info("Reflex overridden to synthesis: message names %r", named_place)
            mode = "synthesis"
            classification["mode"] = mode
            classification["reflex_override"] = named_place

    _log_routing(session_id, mode, classification.get("router_tokens", 0))

    if mode == "reflex":
        result = handle_reflex(latest_user, classification.get("leading_question"))
        messages.append({"role": "assistant", "content": result["reply"]})
        _log_mode_usage(session_id, "reflex", result.get("tokens", 0))
        return {
            "reply": result["reply"],
            "tool_calls": [],
            "messages": messages,
            "mode": "reflex",
        }

    if mode == "memory":
        return _run_full_pipeline(messages, session_id, mode="memory", user_id=user_id)

    # synthesis mode — check cache first
    if mode == "synthesis":
        from src.agent.router import extract_location_from_message
        from src.services.synthesis_cache import get_cached_synthesis, store_synthesis
        from src.storage.database import get_db as _get_db

        db = _get_db()

        loc = extract_location_from_message(latest_user)
        lat = loc.get("lat")
        lng = loc.get("lng")
        location_name = loc.get("location_name")

        # Live-conditions and time-forward questions must never be served from cache.
        _cache_bypass_patterns = (
            # Future windows
            "tomorrow",
            "this weekend",
            "saturday",
            "sunday",
            "in 3 days",
            "next week",
            "forecast",
            "this friday",
            # Present-tense conditions (also require a live fetch, not cached synthesis)
            "right now",
            "today",
            "currently",
            "at the moment",
            "conditions",
            "conditions like",
            "weather",
        )
        _is_time_forward = any(p in latest_user.lower() for p in _cache_bypass_patterns)

        if not _is_time_forward and (lat is not None or location_name is not None):
            cached = get_cached_synthesis(
                db, user_id=user_id, lat=lat, lng=lng, location_name=location_name
            )
            if cached:
                cache_prompt = (
                    f"The user asked: {latest_user}\n\n"
                    f"Here is the stored synthesis for this location "
                    f"(computed {cached.get('computed_at', 'previously')}):\n\n"
                    f"{cached['synthesis']}\n\n"
                    f"Answer the user's question using this synthesis. "
                    f"Be concise and directly address what they asked. "
                    f"If the synthesis doesn't fully answer the question, "
                    f"say so and offer to run a fresh analysis."
                )
                client = get_client()
                resp = client.messages.create(
                    model=HAIKU,
                    max_tokens=600,
                    messages=[{"role": "user", "content": cache_prompt}],
                )
                reply = "".join(b.text for b in resp.content if b.type == "text").strip()
                reply += "\n\n*(Answer from synthesis cache)*"
                messages.append({"role": "assistant", "content": reply})
                _log_routing(
                    session_id,
                    "synthesis_cache_hit",
                    resp.usage.input_tokens + resp.usage.output_tokens,
                )
                return {
                    "reply": reply,
                    "tool_calls": [],
                    "messages": messages,
                    "mode": "synthesis_cache_hit",
                }

        # Cache miss (or no location) — run full pipeline
        result = _run_full_pipeline(messages, session_id, mode="synthesis", user_id=user_id)

        # Store result in cache if we have a location — never cache time-forward responses
        has_location = lat is not None or location_name is not None
        if not _is_time_forward and has_location and result.get("reply"):
            try:
                store_synthesis(
                    db,
                    user_id=user_id,
                    synthesis=result["reply"],
                    lat=lat,
                    lng=lng,
                    location_name=location_name,
                    data_sources=result.get("tool_calls", []),
                )
            except Exception:
                pass  # Non-fatal

        return result

    # Fallback — full pipeline
    return _run_full_pipeline(messages, session_id, mode=mode, user_id=user_id)


def _log_routing(session_id: str, mode: str, tokens: int) -> None:
    try:
        db = get_db()
        db["api_usage"].insert(
            {
                "session_id": session_id,
                "model": HAIKU,
                "input_tokens": tokens,
                "output_tokens": 0,
                "total_tokens": tokens,
                "tool_calls_made": 0,
                "endpoint": f"router:{mode}",
            }
        )
    except Exception:
        pass


def _log_mode_usage(session_id: str, mode: str, tokens: int) -> None:
    try:
        db = get_db()
        db["api_usage"].insert(
            {
                "session_id": session_id,
                "model": HAIKU,
                "input_tokens": 0,
                "output_tokens": tokens,
                "total_tokens": tokens,
                "tool_calls_made": 0,
                "endpoint": f"mode:{mode}",
            }
        )
    except Exception:
        pass


def run_chat() -> None:
    console = Console()

    try:
        client = get_client()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return

    profile = load_profile()
    db = get_db()
    # The CLI is single-user: everything it reads and writes belongs to user 1.
    cli_user_id = 1
    session_id = datetime.now().isoformat()
    start_session(db, session_id)

    trips = recent_trips(db, limit=5)
    home = get_jurisdiction(profile.home_jurisdiction)
    system_prompt = assemble(load_template(), profile, trips, home)
    angler_context = load_context(db)
    if angler_context:
        system_prompt = system_prompt + "\n\n" + format_context_for_prompt(angler_context)

    model = get_model()

    console.print(f"[dim]fishbot — {model} — type /exit to quit[/dim]")
    console.print()

    messages: list[dict] = []
    tools = _tools(profile)
    turn_index = 0

    while True:
        try:
            user_input = console.input("[bold cyan]> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            close_and_update_context(db, session_id, messages, client)
            console.print("[dim]Session saved.[/dim]")
            console.print("[dim]bye[/dim]")
            return

        if not user_input:
            continue

        if user_input.lower() in EXIT_COMMANDS:
            close_and_update_context(db, session_id, messages, client)
            console.print("[dim]Session saved.[/dim]")
            console.print("[dim]bye[/dim]")
            return

        from src.agent.router import classify_message, handle_reflex

        recent = " ".join(
            m["content"][:200] for m in messages[-4:] if isinstance(m.get("content"), str)
        )
        classification = classify_message(user_input, recent)
        mode = classification["mode"]
        _log_routing(session_id, mode, classification.get("router_tokens", 0))

        if mode == "reflex":
            result = handle_reflex(user_input, classification.get("leading_question"))
            reply = result["reply"]
            console.print(reply, markup=False, highlight=False, soft_wrap=True)
            console.print()
            console.print()
            messages.append({"role": "user", "content": user_input})
            save_turn(db, session_id, "user", user_input, turn_index)
            messages.append({"role": "assistant", "content": reply})
            save_turn(db, session_id, "assistant", reply, turn_index)
            _log_mode_usage(session_id, "reflex", result.get("tokens", 0))
            turn_index += 1
            continue

        messages.append({"role": "user", "content": user_input})
        save_turn(db, session_id, "user", user_input, turn_index)

        # Synthesis cache check
        cache_loc: dict = {}
        if mode == "synthesis":
            from src.agent.router import extract_location_from_message
            from src.services.synthesis_cache import get_cached_synthesis, store_synthesis

            cache_loc = extract_location_from_message(user_input)
            c_lat = cache_loc.get("lat")
            c_lng = cache_loc.get("lng")
            c_name = cache_loc.get("location_name")

            if c_lat is not None or c_name is not None:
                cached = get_cached_synthesis(
                    db, user_id=cli_user_id, lat=c_lat, lng=c_lng, location_name=c_name
                )
                if cached:
                    cache_prompt = (
                        f"The user asked: {user_input}\n\n"
                        f"Here is the stored synthesis for this location "
                        f"(computed {cached.get('computed_at', 'previously')}):\n\n"
                        f"{cached['synthesis']}\n\n"
                        f"Answer the user's question using this synthesis. "
                        f"Be concise and directly address what they asked. "
                        f"If the synthesis doesn't fully answer the question, "
                        f"say so and offer to run a fresh analysis."
                    )
                    resp = client.messages.create(
                        model=HAIKU,
                        max_tokens=600,
                        messages=[{"role": "user", "content": cache_prompt}],
                    )
                    reply = "".join(b.text for b in resp.content if b.type == "text").strip()
                    reply += "\n\n*(Answer from synthesis cache)*"
                    console.print(reply, markup=False, highlight=False, soft_wrap=True)
                    console.print()
                    console.print()
                    messages.append({"role": "assistant", "content": reply})
                    save_turn(db, session_id, "assistant", reply, turn_index)
                    _log_routing(
                        session_id,
                        "synthesis_cache_hit",
                        resp.usage.input_tokens + resp.usage.output_tokens,
                    )
                    turn_index += 1
                    continue

        try:
            _agentic_loop(
                client, model, system_prompt, messages, tools, console, session_id=session_id
            )
        except APIError as e:
            console.print(f"[red]API error: {e}[/red]")
            messages.pop()
            continue
        except KeyboardInterrupt:
            console.print()
            console.print("[dim](interrupted)[/dim]")
            messages.pop()
            continue

        # Save assistant reply and store to synthesis cache
        if messages and isinstance(messages[-1].get("content"), str):
            reply = messages[-1]["content"]
            save_turn(db, session_id, "assistant", reply, turn_index)

            if mode == "synthesis" and reply:
                c_lat = cache_loc.get("lat")
                c_lng = cache_loc.get("lng")
                c_name = cache_loc.get("location_name")
                if c_lat is not None or c_name is not None:
                    try:
                        from src.services.synthesis_cache import store_synthesis

                        store_synthesis(
                            db,
                            user_id=cli_user_id,
                            synthesis=reply,
                            lat=c_lat,
                            lng=c_lng,
                            location_name=c_name,
                        )
                    except Exception:
                        pass  # Non-fatal

        turn_index += 1


def _agentic_loop(
    client: Anthropic,
    model: str,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    console: Console,
    session_id: str | None = None,
    user_id: int = 1,
) -> None:
    """Stream a response, handle tool calls, loop until end_turn."""
    db = get_db()

    while True:
        messages[:] = _summarize_history(messages, client)

        content_blocks: list[Any] = []

        with client.messages.stream(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
            tools=tools,
        ) as stream:
            for text in stream.text_stream:
                console.print(text, end="", markup=False, highlight=False, soft_wrap=True)
            final_msg = stream.get_final_message()
            content_blocks = final_msg.content

        usage = final_msg.usage
        db["api_usage"].insert(
            {
                "session_id": session_id,
                "model": final_msg.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.input_tokens + usage.output_tokens,
                "tool_calls_made": len([b for b in content_blocks if b.type == "tool_use"]),
                "endpoint": "chat",
            }
        )

        tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]

        if not tool_use_blocks:
            console.print()
            console.print()
            text = "".join(b.text for b in content_blocks if b.type == "text")
            messages.append({"role": "assistant", "content": text})
            return

        # Tool call detected — execute and continue the loop
        console.print()
        assistant_content = [_normalize_block(b) for b in content_blocks]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict] = []
        for block in tool_use_blocks:
            result = _execute_tool(block.name, block.input, user_id=user_id)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )
            try:
                import json as _json

                db["tool_usage"].insert(
                    {
                        "session_id": session_id,
                        "tool_name": block.name,
                        "input_summary": str(_json.dumps(block.input))[:200],
                        "success": 1,
                    }
                )
            except Exception:
                pass

        messages.append({"role": "user", "content": tool_results})


def _normalize_block(b: Any) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": b.type}


def _execute_tool(name: str, inputs: dict, user_id: int = 1) -> str:
    """Kept as a name because callers and tests import it; the work moved out."""
    return execute_tool(name, inputs, user_id=user_id)


def _tools(profile: Any) -> list[dict]:
    return tool_schemas(profile)


def _reflex_names_a_place(message: str, user_id: int) -> str | None:
    """Whether a reflex-classified message is actually about specific water.

    Never raises: a failure here must fall back to the classifier's decision,
    which is today's behaviour, rather than breaking the turn.
    """
    try:
        from src.services.context.place import mentions_a_place

        return mentions_a_place(get_db(), message, user_id=user_id)
    except Exception:  # noqa: BLE001
        logger.warning("place check failed; keeping the classifier's mode", exc_info=True)
        return None
