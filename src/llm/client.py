"""The shared Anthropic client. The only place one is constructed."""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

_client: Anthropic | None = None


def load_env() -> None:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")


def get_client() -> Anthropic:
    """One client for the process. Raises RuntimeError when no key is configured."""
    global _client
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or key == "your-key-here":
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key "
            "from https://console.anthropic.com/."
        )
    if _client is None or _client.api_key != key:
        _client = Anthropic(api_key=key)
    return _client
