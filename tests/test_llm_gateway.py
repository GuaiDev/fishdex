"""The LLM gateway: one ledger row per call, attributed to the right user."""

import types

import pytest

from src import llm
from src.llm import ledger
from src.llm.pricing import estimate_usd


class _Messages:
    def __init__(self, fail=False):
        self.fail = fail
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError("upstream exploded")
        usage = types.SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=500,
            server_tool_use=types.SimpleNamespace(web_search_requests=2),
        )
        return types.SimpleNamespace(model=kwargs["model"], usage=usage, content=[])


@pytest.fixture
def ledger_db(db_factory, tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(ledger, "_open_db", lambda: db_factory(path))
    return db_factory(path)


def _use(monkeypatch, messages):
    monkeypatch.setattr(
        "src.llm.client.get_client", lambda: types.SimpleNamespace(messages=messages)
    )


def _rows(db):
    return list(db["llm_usage"].rows)


def test_complete_writes_one_row_for_the_asking_user(ledger_db, monkeypatch):
    msgs = _Messages()
    _use(monkeypatch, msgs)
    llm.complete("router", caller="t.router", user_id=7, session_id="s", max_tokens=5)
    (row,) = _rows(ledger_db)
    assert (row["user_id"], row["caller"], row["role"], row["status"]) == (
        7,
        "t.router",
        "router",
        "ok",
    )
    assert row["model"] == msgs.kwargs["model"] == llm.model_for("router")
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (1000, 200, 500)
    assert row["web_searches"] == 2
    assert row["est_usd"] == pytest.approx(estimate_usd(row["model"], 1000, 200, 0, 500, 2))
    assert row["latency_ms"] >= 0


def test_a_failed_call_writes_an_error_row_and_reraises(ledger_db, monkeypatch):
    _use(monkeypatch, _Messages(fail=True))
    with pytest.raises(RuntimeError):
        llm.complete("coaching", caller="t.coach", user_id=3, max_tokens=5)
    (row,) = _rows(ledger_db)
    assert (row["user_id"], row["status"]) == (3, "error")
    assert "upstream exploded" in row["error"]


def test_background_work_is_recorded_with_no_user(ledger_db, monkeypatch):
    _use(monkeypatch, _Messages())
    llm.complete("router", caller="t.batch", user_id=None, max_tokens=5)
    assert _rows(ledger_db)[0]["user_id"] is None


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        llm.model_for("no_such_role")


def test_role_override_from_environment(monkeypatch):
    monkeypatch.setenv("FISHDEX_MODEL_ROUTER", "claude-test-model")
    assert llm.model_for("router") == "claude-test-model"


def test_unpriced_model_is_null_not_free():
    assert estimate_usd("mystery-model", 10, 10) is None


def test_ledger_failure_does_not_fail_the_request(monkeypatch):
    _use(monkeypatch, _Messages())

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ledger, "_open_db", boom)
    before = ledger.write_failures
    llm.complete("router", caller="t", user_id=1, max_tokens=5)
    assert ledger.write_failures == before + 1


@pytest.mark.parametrize(
    "role,variable",
    [
        ("router", "FISHDEX_MODEL_ROUTER"),
        ("chat", "FISHDEX_MODEL_CHAT"),
        ("chat", "CLAUDE_MODEL"),
    ],
)
def test_dotenv_override_used_on_first_call(ledger_db, monkeypatch, tmp_path, role, variable):
    from dotenv import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(f"{variable}=dotenv-model\n")
    monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("FISHDEX_MODEL_CHAT", raising=False)
    monkeypatch.setattr("src.llm.client.load_env", lambda: load_dotenv(env_file))
    messages = _Messages()
    _use(monkeypatch, messages)

    llm.complete(role, caller="t.dotenv", user_id=7, max_tokens=5)

    assert messages.kwargs["model"] == "dotenv-model"
    assert _rows(ledger_db)[0]["model"] == "dotenv-model"


def test_usage_displays_unpriced_calls(ledger_db, monkeypatch):
    from io import StringIO

    from rich.console import Console

    from src.cli import main

    for caller, price in [("unknown", None), ("mixed", None), ("mixed", 0.25)]:
        ledger_db["llm_usage"].insert(
            {
                "caller": caller,
                "role": "router",
                "model": "test",
                "est_usd": price,
            }
        )
    output = StringIO()
    monkeypatch.setattr(main, "get_db", lambda: ledger_db)
    monkeypatch.setattr(main, "console", Console(file=output, width=160))

    main.usage(days=7)

    rendered = output.getvalue()
    assert "Unpriced" in rendered
    assert "unknown" in rendered
    unknown_line = next(line for line in rendered.splitlines() if "unknown" in line)
    assert "unknown" in unknown_line.split()[2:]
    mixed_line = next(line for line in rendered.splitlines() if "mixed" in line)
    assert "0.2500" in mixed_line
    assert mixed_line.split()[-1] == "1"
    day_line = next(line for line in rendered.splitlines() if "0.2500" in line)
    assert day_line.split()[-1] == "2"


class _CachingMessages:
    """A fake API with prompt-caching behaviour: a marked prefix seen before is read."""

    def __init__(self):
        self.seen: set[str] = set()
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        prefix = repr((kwargs.get("tools"), (kwargs.get("system") or [None])[0]))
        marked = any("cache_control" in t for t in kwargs.get("tools") or [])
        hit = marked and prefix in self.seen
        if marked:
            self.seen.add(prefix)
        usage = types.SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=0 if hit or not marked else 4000,
            cache_read_input_tokens=4000 if hit else 0,
            server_tool_use=None,
        )
        return types.SimpleNamespace(model=kwargs["model"], usage=usage, content=[])


TOOLS = [{"name": "a", "input_schema": {}}, {"name": "b", "input_schema": {}}]


def test_chat_marks_tools_and_stable_system_block_and_second_turn_reads_cache(
    ledger_db, monkeypatch
):
    msgs = _CachingMessages()
    _use(monkeypatch, msgs)
    for user_text, per_user in (("hi", "user one"), ("again", "user one")):
        llm.complete(
            "chat",
            caller="t.chat",
            user_id=1,
            max_tokens=5,
            system=[
                {"type": "text", "text": "STABLE"},
                {"type": "text", "text": per_user},
            ],
            tools=TOOLS,
            messages=[{"role": "user", "content": user_text}],
        )
    first, second = msgs.requests
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in first["tools"][0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in first["system"][1]
    rows = _rows(ledger_db)
    assert rows[0]["cache_read_tokens"] == 0 and rows[0]["cache_creation_tokens"] > 0
    assert rows[1]["cache_read_tokens"] > 0


def test_coaching_string_system_becomes_one_cached_block(ledger_db, monkeypatch):
    msgs = _CachingMessages()
    _use(monkeypatch, msgs)
    llm.complete("coaching", caller="t.coach", user_id=1, max_tokens=5, system="RULES", tools=TOOLS)
    assert msgs.requests[0]["system"] == [
        {"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}
    ]


def test_caller_set_breakpoint_is_kept_and_input_is_not_mutated(ledger_db, monkeypatch):
    msgs = _Messages()
    _use(monkeypatch, msgs)
    system = [
        {"type": "text", "text": "A"},
        {"type": "text", "text": "B", "cache_control": {"type": "ephemeral"}},
    ]
    tools = [{"name": "a", "input_schema": {}}]
    llm.complete("chat", caller="t", user_id=1, max_tokens=5, system=system, tools=tools)
    assert "cache_control" not in msgs.kwargs["system"][0]
    assert "cache_control" not in tools[0] and "cache_control" not in system[0]


def test_other_roles_are_not_marked(ledger_db, monkeypatch):
    msgs = _Messages()
    _use(monkeypatch, msgs)
    llm.complete("router", caller="t", user_id=1, max_tokens=5, system="S", tools=TOOLS)
    assert msgs.kwargs["system"] == "S"
    assert all("cache_control" not in t for t in msgs.kwargs["tools"])
