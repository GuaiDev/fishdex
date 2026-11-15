"""Size caps on what a request may hand to a paid model call.

Every limit here exists because the model bills by the token: an uncapped body, history
or photo list is an uncapped bill. Limits are constants, not env settings, so a deploy
cannot quietly lift them.
"""

from fastapi import HTTPException

from src.services.photo_storage import MAX_PHOTO_BYTES

MAX_MESSAGE_CHARS = 4_000
MAX_HISTORY_MESSAGES = 16
MAX_HISTORY_MESSAGE_CHARS = 4_000
MAX_LOG_TEXT_CHARS = 4_000
MAX_CATCHES_JSON_CHARS = 64_000
MAX_PHOTOS = 6  # the primary `photo` plus everything in `photos[]`

# One unit is one chat message or one text parse. A photo adds a vision call on top.
PHOTO_COST = 2

MAX_JSON_BODY_BYTES = 256 * 1024
# Room for the most photos allowed at full size, plus the text fields.
MAX_MULTIPART_BODY_BYTES = MAX_PHOTOS * MAX_PHOTO_BYTES + 1024 * 1024

_HISTORY_ROLES = {"user", "assistant"}


def require_text(value: object, field: str, max_chars: int) -> str:
    """A non-empty string no longer than `max_chars`, else a 400."""
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"'{field}' must be a string")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"'{field}' field is required")
    if len(value) > max_chars:
        raise HTTPException(
            status_code=400, detail=f"'{field}' is too long (limit {max_chars:,} characters)"
        )
    return value


def sanitize_history(history: object) -> list[dict]:
    """Keep only plain user/assistant text turns, the last few, each cut to length.

    Anything else (other roles, tool or image blocks, non-string content) is dropped,
    not repaired: the client is not trusted to write the model's side of the conversation.
    """
    if history is None:
        return []
    if not isinstance(history, list):
        raise HTTPException(status_code=400, detail="'conversation_history' must be a list")
    turns = [
        {"role": m["role"], "content": m["content"][:MAX_HISTORY_MESSAGE_CHARS]}
        for m in history
        if isinstance(m, dict)
        and m.get("role") in _HISTORY_ROLES
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ][-MAX_HISTORY_MESSAGES:]
    while turns and turns[0]["role"] != "user":  # the API wants the first turn to be the user's
        turns.pop(0)
    return turns


class BodySizeLimitMiddleware:
    """Refuse oversized request bodies with 413, before the app reads them.

    Checks Content-Length up front and counts the bytes of chunked bodies as they stream,
    so a client cannot dodge the cap by leaving the header out.
    """

    def __init__(
        self,
        app,
        json_limit: int = MAX_JSON_BODY_BYTES,
        multipart_limit: int = MAX_MULTIPART_BODY_BYTES,
    ):
        self.app = app
        self.json_limit = json_limit
        self.multipart_limit = multipart_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope["headers"]}
        multipart = headers.get(b"content-type", b"").startswith(b"multipart/form-data")
        limit = self.multipart_limit if multipart else self.json_limit

        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > limit:
            await self._refuse(send, limit)
            return

        seen = 0
        refused = False

        async def counting_receive():
            nonlocal seen, refused
            message = await receive()
            if message["type"] == "http.request" and not refused:
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Answer now rather than raising: FastAPI turns any error raised
                    # while reading the body into a 400, which would hide the 413.
                    refused = True
                    await self._refuse(send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not refused:  # the app's own reply to the cut-off request is dropped
                await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _refuse(send, limit: int) -> None:
        body = b'{"detail":"Request body too large (limit %d bytes)."}' % limit
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
