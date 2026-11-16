"""Tests for cost optimization features: token tracking and conversation summarization."""

from pathlib import Path

from sqlite_utils import Database


def test_summarize_history_short_conversation():
    """Short conversations should not be summarized."""
    from src.agent.chat import _summarize_history

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    # Too short — should return unchanged without calling the client
    result = _summarize_history(messages, user_id=1)
    assert result == messages


def test_summarize_history_long_conversation(monkeypatch):
    """Conversations exceeding MAX_TURNS_BEFORE_SUMMARY should be summarized."""
    from unittest.mock import MagicMock

    from src.agent.chat import MAX_TURNS_BEFORE_SUMMARY, _summarize_history

    # Build a conversation longer than the threshold
    messages = []
    for i in range(MAX_TURNS_BEFORE_SUMMARY + 1):
        messages.append({"role": "user", "content": f"User message {i}"})
        messages.append({"role": "assistant", "content": f"Assistant reply {i}"})

    mock_content = MagicMock()
    mock_content.text = "## Conversation context\n- Discussed fishing tactics"
    mock_response = MagicMock()
    mock_response.content = [mock_content]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    monkeypatch.setattr("src.llm.client.get_client", lambda: mock_client)
    result = _summarize_history(messages, user_id=1)

    assert len(result) < len(messages)
    assert result[0]["role"] == "user"
    assert "Conversation context" in result[0]["content"]
    assert result[1]["role"] == "assistant"
    mock_client.messages.create.assert_called_once()


def test_api_usage_table_exists(tmp_path: Path):
    """api_usage table should exist after ensure_schema."""
    from src.storage.database import ensure_schema

    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    tables = [
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    assert "api_usage" in tables


def test_api_usage_table_columns(tmp_path: Path):
    """api_usage table should have the expected columns."""
    from src.storage.database import ensure_schema

    db = Database(tmp_path / "test.db")
    ensure_schema(db)
    cols = {c.name for c in db["api_usage"].columns}
    assert {
        "session_id",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "tool_calls_made",
        "endpoint",
    }.issubset(cols)


def test_system_prompt_loads():
    """System prompt should load without error and contain the new rule sections."""
    from src.agent.system_prompt import load_template

    template = load_template()
    assert len(template) > 100
    assert "Tool call rules" in template
    assert "Response length rules" in template
