"""The one place a model call is made.

Every call goes through `complete` (or `stream`), which picks the model from the
role registry, makes the call on the shared client, and writes one `llm_usage`
row: who asked, which caller, what it cost. Nothing outside this package calls
`messages.create`, so spend cannot hide in a call site that forgot to log.
"""

from src.llm import client as _client
from src.llm.gateway import complete, stream
from src.llm.registry import ROLES, model_for


def get_client():
    """The shared client (looked up at call time, so tests can stub it in one place)."""
    return _client.get_client()


__all__ = ["ROLES", "complete", "get_client", "model_for", "stream"]
