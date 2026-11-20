"""Who is making this request, built once per request at the API edge."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class UserContext(BaseModel):
    """The caller of one request.

    `user_id` is None only for the signed-out public demo (`is_demo`), which has no
    personal data: every per-user lookup given None finds nobody. Everything else
    carries a real id, and nothing downstream may fall back to a default one.
    """

    model_config = ConfigDict(frozen=True)

    user_id: int | None
    username: str = ""
    display_name: str | None = None
    role: str | None = None
    daily_message_limit: int = 50
    is_demo: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "UserContext":
        """Build from a `users` row (or the demo / API-key dicts the auth layer makes)."""
        limit = row.get("daily_message_limit")
        return cls(
            user_id=row.get("id"),
            username=row.get("username") or "",
            display_name=row.get("display_name"),
            role=row.get("role"),
            daily_message_limit=50 if limit is None else limit,
            is_demo=bool(row.get("demo")),
        )
