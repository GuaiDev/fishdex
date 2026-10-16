"""FishBot FastAPI server.

Exposes the chat agent over HTTP for mobile and web clients.
Start with: uv run fishbot serve
"""

import json
import logging
import math
import os
import secrets
import sys
from contextlib import asynccontextmanager

# Configure stdout logging before any other module runs.
# basicConfig is a no-op if handlers are already attached (e.g. a parent process
# configured logging); the explicit setLevel always runs and prevents the root
# logger's default WARNING level from silently dropping INFO messages in
# background tasks on Railway.
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger().setLevel(logging.INFO)

# These sit below basicConfig on purpose — see the comment above. Importing
# FastAPI first lets it attach handlers to the root logger, after which
# basicConfig becomes a no-op and background-task INFO messages vanish on
# Railway. Same reason the PHOTOS_DIR import further down already carries one.
from fastapi import (  # noqa: E402
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.models.pin import (  # noqa: E402
    PinCreate,
    PinLinkCreate,
    PinMerge,
    PinUpdate,
    RecalledVisitCreate,
    SessionUpdate,
    TodaySort,
    TripPinRating,
)
from src.models.weed_report import WeedReportCreate, WeedReportUpdate  # noqa: E402


def verify_api_key(x_api_key: str = Header(None)) -> None:
    """Verify admin API key for protected endpoints.

    Fails closed: if FISHBOT_API_KEY is unset or empty the endpoint refuses with
    503 (server not configured) rather than letting everyone in. A wrong or
    missing X-Api-Key header is 401.
    """
    expected = os.environ.get("FISHBOT_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="FISHBOT_API_KEY is not set on the server; admin endpoints are disabled.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key")


def get_current_user(authorization: str = Header(None)) -> dict:
    """Extract and validate user from Authorization: Bearer <token> header."""
    from src.auth.auth import get_user_from_token
    from src.storage.database import get_db

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.replace("Bearer ", "").strip()
    db = get_db()
    user = get_user_from_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def get_current_user_or_apikey(
    authorization: str = Header(None),
    x_api_key: str = Header(None),
) -> dict:
    """Accept either Bearer token (users) or X-Api-Key (admin fallback for log.html)."""
    from src.auth.auth import get_user_from_token
    from src.storage.database import get_db

    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
        db = get_db()
        user = get_user_from_token(db, token)
        if user:
            return user
    expected = os.environ.get("FISHBOT_API_KEY")
    if expected and x_api_key and secrets.compare_digest(x_api_key.encode(), expected.encode()):
        return {"id": 1, "username": "jason", "role": "admin", "daily_message_limit": 9999}
    raise HTTPException(status_code=401, detail="Not authenticated")


_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Re-assert INFO level after uvicorn's own logging.config.dictConfig() runs.
    # Without this, uvicorn leaves the root logger at WARNING and silently drops
    # every INFO message emitted by background ingest tasks.
    logging.getLogger().setLevel(logging.INFO)

    from src.storage.database import ensure_schema, get_db

    db = get_db()
    ensure_schema(db)

    count = db.execute("SELECT COUNT(*) FROM map_segments").fetchone()[0]
    if count == 0:
        try:
            import json

            map_file = "data/processed/map_data.json"
            if os.path.exists(map_file):
                print("[startup] Importing map segments...")
                with open(map_file) as f:
                    data = json.load(f)
                rows = []
                for feat in data["features"]:
                    coords = feat["geometry"]["coordinates"]
                    p = feat["properties"]
                    rows.append(
                        {
                            "ogf_id": p["ogf_id"],
                            "lat": coords[1],
                            "lng": coords[0],
                            "score_balanced": p.get("untapped_score_balanced"),
                            "score_easy": p.get("untapped_score_easy"),
                            "score_adventure": p.get("untapped_score_adventure"),
                            "habitat_score": p.get("habitat_score"),
                            "access_score": p.get("access_score"),
                            "stream_order": p.get("stream_order"),
                            "watercourse_name": p.get("watercourse_name"),
                            "nearest_named_stream": p.get("nearest_named_stream"),
                            "is_confluence": 1 if p.get("is_confluence_segment") else 0,
                            "connected_to_waterbody": 1 if p.get("connected_to_waterbody") else 0,
                            "observation_pressure": p.get("observation_pressure"),
                            "google_maps_url": p.get("google_maps_url"),
                            "swoop_url": p.get("swoop_url"),
                        }
                    )
                    if len(rows) >= 1000:
                        db["map_segments"].insert_all(rows, ignore=True)
                        db.conn.commit()
                        rows = []
                if rows:
                    db["map_segments"].insert_all(rows, ignore=True)
                    db.conn.commit()
                final = db.execute("SELECT COUNT(*) FROM map_segments").fetchone()[0]
                print(f"[startup] Imported {final:,} map segments.")
        except Exception as e:
            print(f"[startup] Map segment import failed: {e}")

    yield


app = FastAPI(
    title="FishBot API",
    description="Personal fishing intelligence platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (mobile log page)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

from src.services import area_ingest  # noqa: E402

# Catch photos, saved under DATA_DIR/photos by src/services/photo_storage.py
from src.services.photo_storage import PHOTOS_DIR  # noqa: E402

PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=str(PHOTOS_DIR)), name="photos")


def _hour_to_time_of_day(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 9:
        return "dawn"
    if hour < 12:
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    if hour < 20:
        return "evening"
    return "night"


# --- Request / Response models ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    conversation_history: list[ChatMessage] | None = []
    user_id: str | None = "default"


class ChatResponse(BaseModel):
    reply: str
    conversation_history: list[ChatMessage]
    tool_calls_made: list[str] | None = []


# --- Endpoints ---


@app.get("/health")
def health():
    return {"status": "ok", "service": "fishbot"}


@app.post("/auth/signup")
def signup(body: dict):
    """
    Redeem an invite code and create an account.
    Body: {"code": "A3KX9P2Q", "username": "jake", "display_name": "Jake"}
    Returns: {"token": "...", "user_id": N, "username": "..."}
    """
    from src.auth.auth import redeem_invite_code
    from src.storage.database import get_db

    code = body.get("code", "").strip()
    username = body.get("username", "").strip()
    display_name = body.get("display_name", "").strip()

    if not code or not username:
        raise HTTPException(status_code=400, detail="code and username are required")
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(status_code=400, detail="Username must be 2-20 characters")
    if not username.isalnum():
        raise HTTPException(status_code=400, detail="Username must be letters and numbers only")

    db = get_db()
    result = redeem_invite_code(db, code, username, display_name)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return {
        "token": result["token"],
        "user_id": result["user_id"],
        "username": username,
        "display_name": display_name or username,
    }


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    """Return current user info."""
    from src.auth.auth import check_rate_limit
    from src.storage.database import get_db

    db = get_db()
    usage = check_rate_limit(db, user["id"], user.get("daily_message_limit", 50))
    return {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name"),
        "role": user.get("role"),
        "usage_today": usage["used"],
        "daily_limit": usage["limit"],
    }


@app.post("/chat")
def chat(body: dict, user: dict = Depends(get_current_user)):
    """Send a message to FishBot. Body: {"messages": [{"role": "user", "content": "..."}]}"""
    from src.agent.chat import run_chat_api
    from src.auth.auth import check_rate_limit, increment_usage
    from src.storage.database import get_db

    db = get_db()
    limit = user.get("daily_message_limit", 50)
    usage = check_rate_limit(db, user["id"], limit)
    if not usage["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily message limit reached ({limit} messages/day). Try again tomorrow.",
        )

    try:
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="'message' field is required")
        history = body.get("conversation_history", [])
        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": message})
        session_id = body.get("session_id")
        result = run_chat_api(messages, session_id=session_id, user_id=user["id"])
        increment_usage(db, user["id"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _normalize_structured_catches(
    catches_json: str | None, extra_photos: list[dict | None] | None
) -> list[dict]:
    """Parse and sanitize the multi-catch UI's catches_json field into the
    shape trip_logger.log_session expects.

    Never raises — malformed/absent catches_json degrades to "no structured
    catches", which is exactly log_session's existing NL-only behavior, not
    a request failure. extra_photos[i] (already-saved {"path", "url"} dicts
    from /log-trip/photo's `photos[]` field) is attached to catches_json[i]
    by index position, per the multi-photo contract.

    source (optional — "fast_tally" | "detailed", anything else normalized
    to None) and caught_at (optional — the client's ISO tap timestamp) flow
    through to log_session for the fast-tally logging feature: source lets
    log_session tell an anonymous "+1 fish" tap apart from a detailed card
    left with no species, and caught_at preserves the tap's actual time
    since every catch in a session is inserted together at submit time.
    """
    if not catches_json:
        return []
    try:
        raw_list = json.loads(catches_json)
    except (json.JSONDecodeError, TypeError):
        _log.warning("catches_json was not valid JSON — ignoring it, falling back to text only")
        return []
    if not isinstance(raw_list, list):
        _log.warning("catches_json was not a JSON array — ignoring it, falling back to text only")
        return []

    from src.services.trip_logger import parse_size_to_cm

    normalized = []
    for i, entry in enumerate(raw_list):
        if not isinstance(entry, dict):
            continue
        species = entry.get("species")
        species = species.strip() if isinstance(species, str) and species.strip() else None
        # A missing or unusable count is stored as NULL, not guessed: "count
        # it as one fish" is a rule applied where fish are added up.
        try:
            count = int(entry["count"]) if entry.get("count") is not None else None
        except (TypeError, ValueError):
            count = None
        if count is not None and count < 1:
            count = None
        bait = entry.get("bait")
        bait = bait.strip() if isinstance(bait, str) and bait.strip() else None
        size_raw = entry.get("biggest_size_cm")
        if size_raw is None:
            size_raw = entry.get("biggest_size")
        biggest_size_cm = parse_size_to_cm(size_raw)

        source = entry.get("source")
        source = source if source in ("fast_tally", "detailed") else None

        caught_at = entry.get("caught_at")
        caught_at = caught_at.strip() if isinstance(caught_at, str) and caught_at.strip() else None

        photo = extra_photos[i] if extra_photos and i < len(extra_photos) else None
        normalized.append(
            {
                "species": species,
                "count": count,
                "biggest_size_cm": biggest_size_cm,
                "bait": bait,
                "photo_path": photo["path"] if photo else None,
                "photo_url": photo["url"] if photo else None,
                "source": source,
                "caught_at": caught_at,
            }
        )
    return normalized


def _trip_times(started_at: str | None, ended_at: str | None) -> tuple[str | None, str | None]:
    """Validate the "Start fishing" timer's two ends. 400 rather than guess."""
    from pydantic import ValidationError

    from src.models.pin import SessionUpdate

    if not started_at and not ended_at:
        return None, None
    try:
        times = SessionUpdate(started_at=started_at or None, ended_at=ended_at or None)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail="started_at/ended_at must be ISO 8601 times with a timezone, "
            f"ending after they start: {e.errors()[0]['msg']}",
        ) from e
    return (
        times.started_at.isoformat() if times.started_at else None,
        times.ended_at.isoformat() if times.ended_at else None,
    )


def _log_trip_core(
    text: str,
    user: dict,
    photo_lat: float | None = None,
    photo_lng: float | None = None,
    photo_taken_at: str | None = None,
    photo_url: str | None = None,
    photo_path: str | None = None,
    catches_json: str | None = None,
    extra_photos: list[dict | None] | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict:
    """Shared implementation behind /log-trip and /log-trip/photo.

    Photo GPS overrides text-parsed location for the primary stop.
    photo_path (internal disk path, never returned to clients) flows through
    to the catches table for photo-serving bookkeeping; photo_url is the
    public URL clients use to display the image.

    catches_json (optional) carries the multi-catch logging UI's structured
    per-catch count/size/bait/photo — see _normalize_structured_catches and
    trip_logger.log_session. Omitted/empty is a complete no-op: pure
    NL-parsed behavior, unchanged, so existing callers (CLI logging, any
    caller that only ever sent `text`) don't regress.
    """
    from src.services.trip_logger import log_session
    from src.services.trip_parser import parse_session_from_text
    from src.storage.database import ensure_schema, get_db

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="'text' field is required")
    started_at, ended_at = _trip_times(started_at, ended_at)

    db = get_db()
    ensure_schema(db)
    parsed = parse_session_from_text(text, db)
    structured_catches = _normalize_structured_catches(catches_json, extra_photos)

    # Inject photo metadata into the first stop
    if parsed.get("stops") and (photo_lat is not None or photo_taken_at or photo_url):
        first_stop = parsed["stops"][0]

        if photo_lat is not None and photo_lng is not None:
            first_stop["photo_lat"] = photo_lat
            first_stop["photo_lng"] = photo_lng
            if not first_stop.get("lat"):
                first_stop["lat"] = photo_lat
                first_stop["lng"] = photo_lng
                first_stop["location_method"] = "photo_exif"
                first_stop["location_confidence"] = 0.95

        if photo_taken_at:
            first_stop["photo_taken_at"] = photo_taken_at
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(photo_taken_at.replace("Z", "+00:00"))
                first_stop["hour_of_day"] = dt.hour
                first_stop["time_of_day"] = _hour_to_time_of_day(dt.hour)
                if not parsed.get("date"):
                    parsed["date"] = dt.date().isoformat()
            except Exception:
                pass

        if photo_url:
            first_stop["photo_url"] = photo_url
        if photo_path:
            first_stop["photo_path"] = photo_path

    # fallback_* let log_session synthesize a stop itself when
    # parse_session_from_text found none at all (e.g. generic text with no
    # location/narrative content) — see log_session's docstring. This is the
    # same photo/GPS metadata as the "Inject photo metadata into the first
    # stop" block above; that block is a no-op when parsed has no stops yet,
    # which is exactly the case this covers.
    result = log_session(
        parsed,
        db,
        user_id=user["id"],
        structured_catches=structured_catches,
        fallback_lat=photo_lat,
        fallback_lng=photo_lng,
        fallback_photo_taken_at=photo_taken_at,
        fallback_photo_url=photo_url,
        fallback_photo_path=photo_path,
        started_at=started_at,
        ended_at=ended_at,
    )

    # Re-query session_conditions rather than passing log_session's internal
    # conditions_result straight through — that dict is shaped for enrichment
    # diagnostics (raw provider payloads, timeout/error markers), not a
    # client-facing contract. This mirrors the columns GET /sessions already
    # exposes, so the summary card and the sessions list agree on shape.
    conditions_row = next(
        iter(
            db.execute(
                "SELECT air_temp_c, pressure_hpa, anomaly_flag "
                "FROM session_conditions WHERE session_id = ?",
                [result["session_id"]],
            ).fetchall()
        ),
        None,
    )
    conditions = None
    if conditions_row:
        conditions = {
            "air_temp_c": conditions_row[0],
            "pressure_hpa": conditions_row[1],
            "anomaly_flag": conditions_row[2],
        }

    return {
        "status": "logged",
        "session_id": result["session_id"],
        "stops_logged": result["stops_logged"],
        "location_method": parsed["stops"][0].get("location_method")
        if parsed.get("stops")
        else None,
        "location_name": parsed["stops"][0].get("location_name") if parsed.get("stops") else None,
        "photo_url": photo_url,
        "conditions": conditions,
        # Every logged catch starts unconfirmed — species is a text-parser/
        # photo-vision suggestion, not committed fact. Client must show these
        # for the user to confirm/correct via POST /catches/{id}/confirm-species.
        "pending_catches": result.get("pending_catches", []),
        # Every catch inserted this call (confirmed or not) — the FishDex
        # end-of-session summary card's totals/biggest-catch/PB-flag read
        # from this, not just pending_catches (which excludes already-
        # confirmed rows like typed species or fast-tally taps).
        "catches": result.get("catches", []),
        # Per stop: the pin it was linked to without asking (undoable), the
        # pins to choose between when several are in range, or a prompt to
        # pin a spot fished before. None when linking failed.
        "pin_links": result.get("pin_links"),
        "started_at": started_at,
        "ended_at": ended_at,
    }


@app.post("/log-trip")
def log_trip(body: dict, user: dict = Depends(get_current_user_or_apikey)):
    """Log a fishing trip from natural language, with optional photo metadata.

    Body: {
        "text": "natural language trip description",
        "photo_lat": float (optional, from EXIF),
        "photo_lng": float (optional, from EXIF),
        "photo_taken_at": "ISO timestamp" (optional, from EXIF),
        "photo_url": "string" (optional),
        "catches_json": "string" (optional — JSON array of structured
            {species, count, biggest_size_cm|biggest_size, bait} from the
            multi-catch logging UI; see _normalize_structured_catches),
        "started_at", "ended_at": ISO 8601 with timezone (optional — the
            "Start fishing" timer's two ends)
    }
    JSON-only — does not accept an actual photo file. Kept for backward
    compatibility with callers (e.g. /log log.html) that only send EXIF-derived
    metadata. Use POST /log-trip/photo to upload the photo file itself.
    """
    try:
        return _log_trip_core(
            text=body.get("text", ""),
            user=user,
            photo_lat=body.get("photo_lat"),
            photo_lng=body.get("photo_lng"),
            photo_taken_at=body.get("photo_taken_at"),
            photo_url=body.get("photo_url"),
            catches_json=body.get("catches_json"),
            started_at=body.get("started_at"),
            ended_at=body.get("ended_at"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/log-trip/photo")
def log_trip_with_photo(
    text: str = Form(...),
    photo: UploadFile = File(...),
    photo_lat: float | None = Form(None),
    photo_lng: float | None = Form(None),
    photo_taken_at: str | None = Form(None),
    catches_json: str | None = Form(None),
    photos: list[UploadFile] | None = File(default=None),
    started_at: str | None = Form(None),
    ended_at: str | None = Form(None),
    user: dict = Depends(get_current_user_or_apikey),
):
    """Log a fishing trip with an actual photo file (multipart/form-data).

    Form fields: text (required), photo (required file — the session/first-
    stop photo, unchanged), photo_lat/photo_lng/photo_taken_at (optional,
    client-extracted EXIF/device-location fallback), catches_json (optional,
    see /log-trip's docstring), photos[] (optional, additive — extra photo
    files, one per catches_json entry by index position; the multi-catch
    UI doesn't send these yet, only `photo`, but the backend now accepts
    them). Stores photos on the server and returns the primary one's public
    URL.
    """
    from src.services.photo_storage import save_photo

    # Before any file is written, so a refused request leaves no orphan photo.
    started_at, ended_at = _trip_times(started_at, ended_at)
    try:
        saved = save_photo(photo)

        extra_photos: list[dict | None] = []
        for f in photos or []:
            # An absent multipart slot can still arrive as a filename-less
            # UploadFile depending on client — treat that as "no photo for
            # this index" rather than persisting a blank image.
            if not f or not getattr(f, "filename", ""):
                extra_photos.append(None)
                continue
            extra_photos.append(save_photo(f))

        return _log_trip_core(
            text=text,
            user=user,
            photo_lat=photo_lat,
            photo_lng=photo_lng,
            photo_taken_at=photo_taken_at,
            photo_url=saved["url"],
            photo_path=saved["path"],
            catches_json=catches_json,
            extra_photos=extra_photos,
            started_at=started_at,
            ended_at=ended_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
def get_sessions(user: dict = Depends(get_current_user)):
    """Return logged sessions for the current user."""
    from src.storage.catches import get_catches_for_sessions
    from src.storage.database import get_db
    from src.storage.pins import pins_for_sessions

    db = get_db()
    try:
        rows = list(
            db.execute(
                """
            SELECT
                s.id, s.date, s.date_approx, s.overall_notes,
                st.location_name, st.location_text,
                GROUP_CONCAT(DISTINCT je.value) as species_caught,
                sc.air_temp_c, sc.pressure_hpa, sc.anomaly_flag,
                s.rating, s.started_at, s.ended_at
            FROM sessions s
            LEFT JOIN stops st ON st.session_id = s.id
            LEFT JOIN json_each(st.species_caught) je ON 1=1
            LEFT JOIN session_conditions sc ON sc.session_id = s.id
            WHERE s.user_id = ?
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT 50
        """,
                [user["id"]],
            ).fetchall()
        )

        session_ids = [r[0] for r in rows]
        catches_by_session = get_catches_for_sessions(db, session_ids)
        pins_by_session = pins_for_sessions(db, user["id"], session_ids)

        sessions = []
        for r in rows:
            catches = catches_by_session.get(r[0], [])
            sessions.append(
                {
                    "id": r[0],
                    "date": r[1] or r[2],
                    "notes": r[3],
                    "location": r[4] or r[5] or "Unknown location",
                    "species_caught": [s.strip() for s in (r[6] or "").split(",") if s.strip()],
                    "conditions": {
                        "air_temp_c": r[7],
                        "pressure_hpa": r[8],
                        "anomaly_flag": r[9],
                    }
                    if r[7]
                    else None,
                    # NULL = not rated, never a low rating. Times are absent
                    # for trips logged before the timer was saved.
                    "rating": r[10],
                    "started_at": r[11],
                    "ended_at": r[12],
                    # The pins this trip's stops link to, with the trip's
                    # rating at each (per pin on a multi-pin trip).
                    "pins": pins_by_session.get(r[0], []),
                    # Catches predating this feature have no photo — photo_url is
                    # simply None, callers must handle the no-photo case.
                    "catches": [
                        {
                            "species": c["species"],
                            "count": c["count"],
                            "biggest_size": c["biggest_size"],
                            "biggest_size_cm": c["biggest_size_cm"],
                            "bait": c["bait"],
                            "photo_url": c["photo_url"],
                        }
                        for c in catches
                    ],
                }
            )
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Regional fallback when a user has no GPS grant and no located trips yet —
# mirrors the frontend map's own default center (web/src/screens/Map.jsx),
# kept in sync deliberately so a brand-new user still sees real, plausible
# conditions rather than an empty credibility panel on the Chat screen.
_DEFAULT_REGION = {"lat": 43.5, "lng": -79.8, "name": "Southern Ontario"}


@app.get("/conditions")
def get_conditions(
    user: dict = Depends(get_current_user),
    lat: float | None = None,
    lng: float | None = None,
):
    """Live conditions for the Chat coach's "showing its work" evidence panel.

    Reads through the *same* weather functions the chat agent's own
    get_conditions / get_pressure_trend tools call (src.services.weather ->
    src.ingest.global.weather), so the panel shows literally the data the
    coach reasons from, never a parallel source that could drift.

    Location resolves via a fallback chain so the panel is never empty:
      1. explicit lat/lng (frontend passes these only when the browser
         already has a *granted* geolocation permission — never prompts),
      2. the user's most recent trip that has real coordinates,
      3. a regional default (_DEFAULT_REGION) for brand-new users.
    The response labels which of these it used, and the real place name, so
    the UI can be honest about whose conditions it's showing.
    """
    import importlib

    # "global" is a Python keyword, so this subpackage can't be imported with a
    # normal `from src.ingest.global import weather` — same reason the weather
    # service itself reaches it via importlib. Reuse that exact module so this
    # endpoint reads through the identical code path the coach's tools use.
    _weather = importlib.import_module("src.ingest.global.weather")
    from src.services.weather import _FISHING_NOTES
    from src.storage.database import get_db

    if lat is not None and lng is not None:
        loc = {"lat": lat, "lng": lng, "name": "your current location", "source": "gps"}
    else:
        db = get_db()
        row = db.execute(
            """
            SELECT st.lat, st.lng, st.location_name, st.location_text
            FROM sessions s
            LEFT JOIN stops st ON st.session_id = s.id
            WHERE s.user_id = ? AND st.lat IS NOT NULL AND st.lng IS NOT NULL
            ORDER BY s.id DESC
            LIMIT 1
            """,
            [user["id"]],
        ).fetchone()
        if row and row[0] is not None:
            loc = {
                "lat": row[0],
                "lng": row[1],
                "name": row[2] or row[3] or "your last trip",
                "source": "last_trip",
            }
        else:
            loc = {**_DEFAULT_REGION, "source": "regional_default"}

    try:
        conditions = _weather.get_current_conditions(loc["lat"], loc["lng"])
        trend = _weather.compute_pressure_trend(loc["lat"], loc["lng"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Conditions unavailable: {e}")

    return {
        "location": {
            "name": loc["name"],
            "lat": loc["lat"],
            "lng": loc["lng"],
            "source": loc["source"],
        },
        "time": conditions.time.isoformat(),
        "temperature_c": conditions.temperature_c,
        "humidity_pct": conditions.humidity_pct,
        "wind_speed_kmh": conditions.wind_speed_kmh,
        "cloud_cover_pct": conditions.cloud_cover_pct,
        "weather_code": conditions.weather_code,
        "pressure_hpa": conditions.pressure_hpa,
        "pressure_trend": trend.trend,
        "pressure_delta_24h_hpa": trend.delta_24h_hpa,
        "pressure_note": _FISHING_NOTES[trend.trend],
    }


@app.get("/fishdex")
def get_fishdex(user: dict = Depends(get_current_user)):
    """Return real caught-species + regional pool data for the My FishDex screen.

    Jurisdiction is hardcoded to CA-ON for now (this app is Ontario-first per
    CLAUDE.md and there's no per-user jurisdiction field on the profile yet) —
    a real multi-jurisdiction pool would key this off the user's home
    jurisdiction instead.
    """
    from src.services.fishdex import get_fishdex_data
    from src.storage.database import get_db

    db = get_db()
    try:
        return get_fishdex_data(db, user["id"], jurisdiction="CA-ON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/catches/pending")
def get_pending_catches_endpoint(user: dict = Depends(get_current_user)):
    """Catches awaiting species confirmation — text-parsed and/or photo-vision
    suggested, not yet reviewed by the user. Drives the confirm/correct UI."""
    from src.storage.catches import get_pending_catches_for_user
    from src.storage.database import get_db

    db = get_db()
    rows = get_pending_catches_for_user(db, user["id"])
    pending = []
    for r in rows:
        suggested = json.loads(r["suggested_species"]) if r.get("suggested_species") else []
        pending.append(
            {
                "catch_id": r["id"],
                "species": r["species"],
                "suggested_species": suggested,
                "photo_url": r.get("photo_url"),
                "created_at": r.get("created_at"),
            }
        )
    return {"pending": pending}


@app.post("/catches/{catch_id}/confirm-species")
def confirm_catch_species_endpoint(
    catch_id: int, body: dict, user: dict = Depends(get_current_user)
):
    """Commit the user's confirmed/corrected species for a catch.

    This is the only way a catch's species_confirmed flag becomes true — see
    the FishDex hallucination fix: nothing from the NL parser or photo vision
    is ever treated as fact without this step.
    """
    from src.storage.catches import confirm_catch_species
    from src.storage.database import get_db

    species = (body.get("species") or "").strip()
    if not species:
        raise HTTPException(status_code=400, detail="'species' field is required")

    db = get_db()
    result = confirm_catch_species(db, catch_id, user["id"], species)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Catch not found")
    return {
        "status": "confirmed",
        "catch_id": catch_id,
        "species": species,
        # Surfaced for the end-of-session summary card: the biggest/only
        # detailed catch in a session is often still pending this exact call
        # when the card first renders, so whether it's a new personal best
        # only becomes knowable here, not at initial log-trip time.
        "is_new_pb": result["is_new_pb"],
    }


@app.get("/log")
def log_page():
    """Serve the mobile trip logging page."""
    return FileResponse(os.path.join(_static_dir, "log.html"))


# Local-dev-only convenience login page — deliberately registered as a
# route only when NOT running on Railway, not just "logically blocked"
# inside the handler. Railway injects RAILWAY_ENVIRONMENT into every
# deployment automatically (confirmed present in this project's Railway
# variables), so this needs zero local config to resolve correctly: unset
# locally -> route exists, set on Railway -> route is never added to the
# app's route table at all. Hitting /admin-login on the deployed app 404s
# exactly like any other undefined path — there is no code path there to
# audit or accidentally re-enable, since app.get() was simply never called
# for it in that process. The underlying POST /admin/token this page wraps
# is unaffected either way; it stays reachable (and properly gated by
# FISHBOT_API_KEY, see verify_api_key) on both Railway and local, since
# that endpoint already existed before this page and other legitimate
# callers (e.g. re-generating an admin token from a script) may need it in
# production too. Only the convenience *page* is scoped down.
if not os.environ.get("RAILWAY_ENVIRONMENT"):

    @app.get("/admin-login")
    def admin_login_page():
        """Serve the personal admin-login shortcut page (local dev only).

        Convenience only, not a new auth path: it just wraps the existing
        POST /admin/token in a form instead of a manual curl + devtools
        localStorage paste, and optionally remembers the API key on this
        device so a repeat visit re-logs-in with zero typing. Whoever holds
        the API key still gets full admin access — this page doesn't change
        that, it just makes using it less tedious locally.
        """
        return FileResponse(os.path.join(_static_dir, "admin-login.html"))


@app.post("/admin/token")
def get_admin_token(_: None = Depends(verify_api_key)):
    """Generate a fresh admin token. Protected by API key only."""
    from datetime import datetime, timedelta

    from src.storage.database import get_db

    db = get_db()
    existing = list(db.execute("SELECT id, username FROM users WHERE id = 1").fetchall())
    if not existing:
        raise HTTPException(status_code=404, detail="Admin user (id=1) not found in the database.")

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=90)).isoformat()
    db.execute(
        "INSERT INTO user_sessions (user_id, token, expires_at, last_used_at) VALUES (1, ?, ?, ?)",
        [token, expires, datetime.now().isoformat()],
    )
    db.conn.commit()
    return {"token": token, "bearer": f"Bearer {token}"}


@app.get("/admin/db-stats")
def get_db_stats(_: None = Depends(verify_api_key)):
    """Diagnostic: observation table stats by source. Temporary endpoint."""
    from src.storage.database import DB_PATH, get_db

    db = get_db()
    rows = db.execute("""
        SELECT
            source,
            COUNT(*)            AS count,
            MIN(lat)            AS lat_min,
            MAX(lat)            AS lat_max,
            MIN(lng)            AS lng_min,
            MAX(lng)            AS lng_max,
            MIN(observed_on)    AS date_min,
            MAX(observed_on)    AS date_max
        FROM observations
        GROUP BY source
        ORDER BY COUNT(*) DESC
    """).fetchall()
    obs_cols = [
        "source",
        "count",
        "lat_min",
        "lat_max",
        "lng_min",
        "lng_max",
        "date_min",
        "date_max",
    ]

    gbif_rows = db.execute("""
        SELECT
            'gbif'              AS source,
            COUNT(*)            AS count,
            MIN(lat)            AS lat_min,
            MAX(lat)            AS lat_max,
            MIN(lng)            AS lng_min,
            MAX(lng)            AS lng_max,
            MIN(observed_on)    AS date_min,
            MAX(observed_on)    AS date_max
        FROM gbif_observations
    """).fetchall()
    gbif_cols = [
        "source",
        "count",
        "lat_min",
        "lat_max",
        "lng_min",
        "lng_max",
        "date_min",
        "date_max",
    ]

    return {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "data_dir_env": os.environ.get("DATA_DIR", "NOT SET"),
        "observations_by_source": [dict(zip(obs_cols, r)) for r in rows],
        "gbif_by_source": [dict(zip(gbif_cols, r)) for r in gbif_rows],
    }


@app.get("/admin/dashboard")
def admin_dashboard(_: None = Depends(verify_api_key)):
    """Admin usage dashboard: users, message volume, top queried locations,
    tool call frequency, ingest coverage, catch-level stats (volume, species
    mix, personal bests, catches per session), and an approximate
    Sonnet-vs-Haiku API cost estimate. Protected by X-Api-Key.
    """
    from src.services.admin_dashboard import build_dashboard
    from src.storage.database import get_db

    db = get_db()
    return build_dashboard(db)


@app.post("/admin/invite")
def create_invite(body: dict, user: dict = Depends(get_current_user)):
    """Generate an invite code. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    from src.auth.auth import generate_invite_code
    from src.storage.database import get_db

    db = get_db()
    note = body.get("note", "")
    code = generate_invite_code(db, created_by=user["id"], note=note)
    return {"code": code, "note": note}


@app.get("/admin/invites")
def list_invites(user: dict = Depends(get_current_user)):
    """List all invite codes. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    from src.storage.database import get_db

    db = get_db()
    codes = list(db["invite_codes"].rows)
    return {"codes": codes}


@app.get("/admin/users")
def list_users(user: dict = Depends(get_current_user)):
    """List all users and their usage. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    from src.storage.database import get_db

    db = get_db()
    users = list(
        db.execute("""
        SELECT u.id, u.username, u.display_name, u.role,
               u.created_at, u.last_seen_at,
               COALESCE(SUM(du.message_count), 0) as total_messages
        FROM users u
        LEFT JOIN daily_usage du ON du.user_id = u.id
        GROUP BY u.id
        ORDER BY u.id
    """).fetchall()
    )
    return {
        "users": [
            {
                "id": r[0],
                "username": r[1],
                "display_name": r[2],
                "role": r[3],
                "created_at": r[4],
                "last_seen_at": r[5],
                "total_messages": r[6],
            }
            for r in users
        ]
    }


@app.post("/ingest")
def ingest(body: dict):
    """Ingest YouTube content for a search query.

    Body: {"query": "channel catfish Grand River", "max_videos": 10}
    Admin/personal use — add auth before making public.
    """
    try:
        from src.ingest.community.youtube_ingest import ingest_query
        from src.storage.database import get_db

        query = body.get("query", "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="'query' field is required")

        max_videos = body.get("max_videos", 10)
        db = get_db()
        result = ingest_query(query, db, max_videos=max_videos)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/data")
def ingest_data(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger global data ingest for a location (runs in background).

    Body: {
        "lat": float,
        "lng": float,
        "radius_km": float (optional, default 50),
        "days_back": int | null (optional, default 90; 0 or null = all history),
        "label": str (optional, human-readable name for logging)
    }
    Runs iNaturalist, GBIF, WSC, and OSM data sources for the given location.
    Returns 202 immediately; ingest runs after the response is sent.
    Protected by X-Api-Key header.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")
    days_back = body.get("days_back", 90) or None  # 0 or null → None → no date filter

    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")

    background_tasks.add_task(area_ingest.run_global_ingest, lat, lng, radius_km, label, days_back)

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "ingest started in background",
        },
    )


@app.post("/ingest/data-bc")
def ingest_data_bc(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger BC-specific data ingest for a location (runs in background).

    Body: {
        "lat": float,
        "lng": float,
        "radius_km": float (optional, default 50),
        "label": str (optional, human-readable name for logging)
    }
    Runs FWA stream network, FISS fish observations, and BC EnMoDS water quality.
    Returns 202 immediately; ingest runs after the response is sent.
    Global sources (iNat, GBIF, WSC, OSM) are handled by /ingest/data.
    Protected by X-Api-Key header.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")

    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")

    background_tasks.add_task(area_ingest.run_bc_ingest, lat, lng, radius_km, label)

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "ingest started in background",
        },
    )


@app.post("/ingest/data-ab")
def ingest_data_ab(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger Alberta-specific data ingest for a location (runs in background).

    Body: {"lat": float, "lng": float, "radius_km": float (optional), "label": str (optional)}
    Runs AB stocking, regulations, water quality.
    Returns 202 immediately. Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")
    background_tasks.add_task(area_ingest.run_ab_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "AB ingest started in background",
        },
    )


@app.post("/ingest/data-qc")
def ingest_data_qc(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger Quebec-specific data ingest for a location (runs in background).

    Body: {"lat": float, "lng": float, "radius_km": float (optional), "label": str (optional)}
    Runs QC species ranges, regulations, water quality.
    Returns 202 immediately. Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")
    background_tasks.add_task(area_ingest.run_qc_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "QC ingest started in background",
        },
    )


@app.post("/ingest/data-mb")
def ingest_data_mb(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger Manitoba-specific data ingest for a location (runs in background).

    Body: {"lat": float, "lng": float, "radius_km": float (optional), "label": str (optional)}
    Runs MB stocking and the WaLLAS stream network.
    Returns 202 immediately. Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")
    background_tasks.add_task(area_ingest.run_mb_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "MB ingest started in background",
        },
    )


@app.post("/ingest/data-sk")
def ingest_data_sk(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger Saskatchewan-specific data ingest for a location (runs in background).

    Body: {"lat": float, "lng": float, "radius_km": float (optional), "label": str (optional)}
    Runs SK stream network, fish observations, stocking, and water quality.
    Returns 202 immediately. Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 50.0)
    label = body.get("label", f"{lat},{lng}")
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")
    background_tasks.add_task(area_ingest.run_sk_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "SK ingest started in background",
        },
    )


def _run_national_ingest(
    lat: float | None, lng: float | None, radius_km: float, label: str
) -> None:
    """Background task: run national/federal ingest adapters."""
    if lat is not None and lng is not None:
        _log.info(
            "[%s] National ingest started — lat=%.4f lng=%.4f radius=%.0fkm",
            label,
            lat,
            lng,
            radius_km,
        )
    else:
        _log.info("[%s] National ingest started — no location filter (national scope)", label)
    from src.storage.database import ensure_schema, get_db

    db = get_db()
    ensure_schema(db)

    _log.info("[%s] DFO SAR range: fetching", label)
    try:
        from src.services.national_ingest import ingest_dfo_sar_range

        n = ingest_dfo_sar_range()
        _log.info("[%s] DFO SAR range: %d records stored", label, n)
    except Exception:
        _log.exception("[%s] DFO SAR range fetch failed", label)

    _log.info("[%s] CABIN (all provinces): fetching", label)
    try:
        from src.services.national_ingest import ingest_cabin_all_provinces

        n = ingest_cabin_all_provinces()
        _log.info("[%s] CABIN: %d samples stored", label, n)
    except Exception:
        _log.exception("[%s] CABIN fetch failed", label)

    if lat is not None and lng is not None:
        _log.info("[%s] DFO critical habitat: fetching", label)
        try:
            from src.services.national_ingest import ingest_dfo_critical_habitat

            n = ingest_dfo_critical_habitat(lat, lng, radius_km)
            _log.info("[%s] DFO critical habitat: %d records stored", label, n)
        except Exception:
            _log.exception("[%s] DFO critical habitat fetch failed", label)

        _log.info("[%s] DataStream water quality: fetching", label)
        try:
            from src.services.national_ingest import ingest_datastream_water_quality

            n = ingest_datastream_water_quality(lat, lng, radius_km)
            _log.info("[%s] DataStream: %d readings stored", label, n)
        except Exception:
            _log.exception("[%s] DataStream fetch failed", label)

    _log.info("[%s] National ingest complete", label)


@app.post("/ingest/data-national")
def ingest_data_national(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger national/federal data ingest (runs in background).

    Body: {"lat": float (optional), "lng": float (optional),
           "radius_km": float (optional, default 100), "label": str (optional)}
    Always runs: DFO SAR range, CABIN (all provinces).
    When lat+lng provided: also runs DFO critical habitat and DataStream water quality.
    Returns 202 immediately. Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 100.0)
    label = body.get("label", f"{lat},{lng}" if lat is not None and lng is not None else "national")
    background_tasks.add_task(_run_national_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "National ingest started in background",
        },
    )


@app.post("/ingest/data-tidal")
def ingest_data_tidal(
    body: dict,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Trigger CHS tidal predictions ingest for a coastal location (runs in background).

    Body: {"lat": float, "lng": float,
           "radius_km": float (optional, default 100), "label": str (optional)}
    Runs CHS SINE API for nearby tidal stations and their high/low tide predictions.
    Relevant for BC coast, NS, NB, PEI. Returns 202 immediately.
    Protected by X-Api-Key.
    """
    lat = body.get("lat")
    lng = body.get("lng")
    radius_km = body.get("radius_km", 100.0)
    label = body.get("label", f"{lat},{lng}")
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat and lng are required")
    background_tasks.add_task(area_ingest.run_tidal_ingest, lat, lng, radius_km, label)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "label": label,
            "message": "Tidal ingest started in background",
        },
    )


@app.get("/map/segments")
def get_map_segments(
    north: float,
    south: float,
    east: float,
    west: float,
    mode: str = "balanced",
    limit: int = 300,
    user: dict = Depends(get_current_user),
):
    """Return stream segments within viewport bounds for explore mode."""
    from src.storage.database import get_db

    score_col = {
        "easy": "score_easy",
        "adventure": "score_adventure",
    }.get(mode, "score_balanced")

    db = get_db()
    rows = list(
        db.execute(
            f"""
        SELECT ogf_id, lat, lng, {score_col} as score,
               watercourse_name, nearest_named_stream,
               stream_order,
               is_confluence, connected_to_waterbody,
               google_maps_url, swoop_url,
               habitat_score, access_score
        FROM map_segments
        WHERE lat BETWEEN ? AND ?
          AND lng BETWEEN ? AND ?
          AND {score_col} IS NOT NULL
        ORDER BY {score_col} DESC
        LIMIT ?
    """,
            [south, north, west, east, limit],
        ).fetchall()
    )

    cols = [
        "ogf_id",
        "lat",
        "lng",
        "score",
        "watercourse_name",
        "nearest_named_stream",
        "stream_order",
        "is_confluence",
        "connected_to_waterbody",
        "google_maps_url",
        "swoop_url",
        "habitat_score",
        "access_score",
    ]
    return {
        "segments": [dict(zip(cols, r)) for r in rows],
        "count": len(rows),
        "mode": mode,
        "bounds": {"north": north, "south": south, "east": east, "west": west},
    }


@app.get("/map/stretches")
def get_map_stretches(
    jurisdiction: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Named fishing stretches for the explore map's low-zoom level, as GeoJSON.

    Built by `make build-stretches` from data/curation/; an empty collection
    means the build has not been run against this database.
    """
    from src.services.stretches import stretches_geojson
    from src.storage.database import get_db

    return stretches_geojson(get_db(), jurisdiction)


@app.get("/map/stretches/{stretch_id}/anchors")
def get_map_stretch_anchors(stretch_id: str, user: dict = Depends(get_current_user)):
    """One stretch's anchors and segment lines for the explore map's zoomed level.

    Point features are confluences, dams and their tailwaters, lake mouths and
    access points; LineString features are the stretch's own segments. Built by
    `make build-stretches` (or `make build-anchors`); 404 for a stretch that has
    not been built, an empty `features` list for one built without anchors.
    """
    from src.services.stretch_anchors import stretch_anchors_geojson
    from src.storage.database import get_db

    collection = stretch_anchors_geojson(get_db(), stretch_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=f"No stretch named {stretch_id!r}")
    return collection


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        parts = tuple(float(p) for p in bbox.split(","))
    except ValueError:
        parts = ()
    if len(parts) != 4 or not (parts[0] < parts[2] and parts[1] < parts[3]):
        raise HTTPException(status_code=422, detail="bbox must be min_lon,min_lat,max_lon,max_lat")
    return parts


def _parse_spot_filters(
    bbox: str | None,
    kind: str | None,
    tier: int,
    species: str | None = None,
    near: str | None = None,
) -> tuple[
    tuple[float, float, float, float] | None,
    list[str] | None,
    str | None,
    tuple[float, float, float | None] | None,
]:
    from src.models.spot import SpotKind

    box = _parse_bbox(bbox)
    kinds = None
    if kind:
        kinds = [k.strip() for k in kind.split(",") if k.strip()]
        valid = set(SpotKind.__args__)
        bad = [k for k in kinds if k not in valid]
        if bad:
            raise HTTPException(
                status_code=422, detail=f"unknown kind {bad[0]!r}; one of {sorted(valid)}"
            )
    if tier not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="tier must be 1, 2 or 3")
    near_t = None
    if near:
        try:
            parts = tuple(float(p.strip()) for p in near.split(","))
            if len(parts) not in (2, 3) or not all(math.isfinite(p) for p in parts):
                raise ValueError
            nlat, nlon = parts[0], parts[1]
            nr = parts[2] if len(parts) == 3 else None
            if not (-90 <= nlat <= 90 and -180 <= nlon <= 180) or (nr is not None and nr <= 0):
                raise ValueError
            near_t = (nlat, nlon, nr)
        except Exception:
            raise HTTPException(status_code=422, detail="near must be lat,lon or lat,lon,radius_km")
    return box, kinds, species, near_t


@app.get("/map/spots/labels")
def get_map_spot_labels(user: dict = Depends(get_current_user)):
    """Every user-facing word the spots map shows: chips, one-liners, layer names, and the
    labelled general-reasoning paragraph per kind. The web app reads this; it hardcodes none."""
    from src.services.spot_labels import labels_payload

    return labels_payload()


@app.get("/terms")
def get_terms(user: dict = Depends(get_current_user)):
    """The term registry: every map/habitat concept with its term_id, plain wording,
    technical wording, a factual definition and where the map gets that feature type.
    Which wording a person sees comes from GET /profile/presentation, not from here."""
    from src.services.terms import terms_payload

    return {"terms": terms_payload()}


# ── angler profile ──────────────────────────────────────────────────────────
# Every route reads the user from the token, never from the body or the path.


@app.get("/profile")
def get_profile(user: dict = Depends(get_current_user)):
    """The angler profile: orientation, size and small-species focus, and literacy per
    concept, each with its inferred and declared parts (value + source + weight) and the
    evidence behind it. For settings and for checking what the app has inferred."""
    from src.services.context import angler_profile
    from src.storage.database import get_db

    return angler_profile(get_db(), user_id=user["id"]).model_dump()


@app.get("/profile/presentation")
def get_profile_presentation(user: dict = Depends(get_current_user)):
    """Per-element prominence (0..1) and label wording mode for the map, dex, log form
    and pins. Facts are never filtered; components decide how loudly to render."""
    from src.services.context import angler_profile
    from src.services.presentation import presentation
    from src.storage.database import get_db

    return presentation(angler_profile(get_db(), user_id=user["id"]))


@app.post("/profile/concept-events", status_code=201)
def post_concept_event(body: dict, user: dict = Depends(get_current_user)):
    """Record literacy evidence: {"term_id": "confluence", "kind": "definition_opened" |
    "seen_on_map", "ref": optional id}. Folded in at the next logged session."""
    from src.storage import angler_profile as store
    from src.storage.database import get_db

    try:
        event_id = store.record_event(
            get_db(),
            user["id"],
            str(body.get("term_id", "")),
            str(body.get("kind", "")),
            body.get("ref"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": event_id}


@app.put("/profile/declared/{key}")
def put_declared_value(key: str, body: dict, user: dict = Depends(get_current_user)):
    """Write a declared value: {"value": 0..1, "weight": 0..MAX (default 3), "override": bool}.
    A questionnaire writes a starting value that fades as trips are logged; settings
    writes an override that holds until cleared. Keys: orientation, size_focus,
    micro_focus, concept:<term_id>."""
    from src.storage import angler_profile as store
    from src.storage.database import get_db

    try:
        value = float(body["value"])
        weight = float(body.get("weight", DECLARED_DEFAULT_WEIGHT))
        if not 0.0 <= value <= 1.0:
            raise ValueError("value must be 0..1")
        row = store.set_declared(
            get_db(), user["id"], key, value, weight, is_override=bool(body.get("override"))
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"key": key, **row.model_dump()}


@app.delete("/profile/declared/{key}")
def delete_declared_value(key: str, user: dict = Depends(get_current_user)):
    """Clear a declared value or override; the value goes back to behaviour alone."""
    from src.storage import angler_profile as store
    from src.storage.database import get_db

    try:
        removed = store.clear_declared(get_db(), user["id"], key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="no declared value for that key")
    return {"key": key, "removed": True}


# A declared starting value counts as this many sessions of evidence.
DECLARED_DEFAULT_WEIGHT = 3.0


@app.get("/map/spots")
def get_map_spots(
    bbox: str | None = None,
    kind: str | None = None,
    tier: int = 2,
    jurisdiction: str | None = None,
    species: str | None = None,
    near: str | None = None,
    new_to_me: bool = False,
    response: Response = None,
    user: dict = Depends(get_current_user),
):
    """Spots for the explore map, as GeoJSON points.

    `bbox` is min_lon,min_lat,max_lon,max_lat. `kind` is one or more comma-separated
    kinds (river_mouth, creek_junction, below_dam, public_fishing, quiet_water).
    `tier` is the highest tier to include, cumulative: 1 headline spots only, 2 adds
    the normal ones (default), 3 everything. Built by `make build-spots` (and at the
    end of `make build-anchors`); an empty collection means it has not been run.
    `new_to_me=true` keeps only spots with a recorded species the caller has not caught
    (confirmed catches only), and is computed for the caller alone. It needs a `bbox`.
    """
    from src.services.spots import spots_geojson
    from src.storage.database import get_db

    box, kinds, species_f, near_f = _parse_spot_filters(bbox, kind, tier, species, near)
    if new_to_me:
        if box is None:
            raise HTTPException(status_code=422, detail="new_to_me needs a bbox")
        # Per-user answer: never let a browser or proxy keep it for the next person.
        response.headers["Cache-Control"] = "private, no-store"
    return spots_geojson(
        get_db(),
        box,
        kinds,
        tier,
        jurisdiction,
        species=species_f,
        near=near_f,
        new_to_me=new_to_me,
        user_id=user["id"],
    )


@app.get("/map/spots/{spot_id}")
def get_map_spot(spot_id: str, response: Response, user: dict = Depends(get_current_user)):
    """One spot in full: title, structure facts, separate signals, nearby parking and
    bridges, sources, the other spots on the same river, and `card`: the four resolved
    blocks a tap reveals (why it's interesting, you might find, why fish gather here,
    getting there) with every string written. The card is the same for every user except
    its "new to you" hook and block, which come from the caller's own confirmed catches.
    404 for an unknown id."""
    from src.services.spots import get_spot_detail
    from src.storage.database import get_db

    detail = get_spot_detail(get_db(), spot_id, user["id"])
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No spot with id {spot_id!r}")
    # The card now depends on who is asking (the "new to you" hook), and a catch
    # confirmed a minute ago must show: no caching at all, shared or private.
    response.headers["Cache-Control"] = "private, no-store"
    return detail


@app.get("/map/water-bodies")
def get_map_water_bodies(
    bbox: str | None = None,
    type: str | None = None,
    jurisdiction: str | None = None,
    user: dict = Depends(get_current_user),
):
    """The rivers, creeks and lakes spots sit on, as GeoJSON.

    `bbox` is min_lon,min_lat,max_lon,max_lat; a water body is in view when its box
    overlaps it. `type` is one or more comma-separated types (river, creek, lake, pond,
    great_lake). A river's geometry is its simplified line; a lake with no outline on
    file has `geometry: null`. Built with the spots by `make build-spots`.
    """
    from src.models.water_body import WaterBodyType
    from src.services.water_bodies import water_bodies_geojson
    from src.storage.database import get_db

    box = _parse_bbox(bbox)
    types = None
    if type:
        types = [t.strip() for t in type.split(",") if t.strip()]
        valid = set(WaterBodyType.__args__)
        bad = [t for t in types if t not in valid]
        if bad:
            raise HTTPException(
                status_code=422, detail=f"unknown type {bad[0]!r}; one of {sorted(valid)}"
            )
    return water_bodies_geojson(get_db(), box, types, jurisdiction)


@app.get("/map/water-bodies/{water_body_id}")
def get_map_water_body(water_body_id: str, user: dict = Depends(get_current_user)):
    """One water body in full: name and where the name came from, its shape, the
    curated stretches it is made of, and every spot on it. 404 for an unknown id."""
    from src.services.water_bodies import get_water_body_detail
    from src.storage.database import get_db

    detail = get_water_body_detail(get_db(), water_body_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No water body with id {water_body_id!r}")
    return detail


@app.get("/map/layers/getting-there")
def get_map_layers_getting_there(
    bbox: str | None = None,
    jurisdiction: str | None = None,
    user: dict = Depends(get_current_user),
):
    from src.services.spots import getting_there_geojson
    from src.storage.database import get_db

    box = _parse_bbox(bbox)
    return getting_there_geojson(get_db(), box, jurisdiction)


@app.get("/map/layers/bridges")
def get_map_layers_bridges(
    bbox: str | None = None,
    jurisdiction: str | None = None,
    user: dict = Depends(get_current_user),
):
    from src.services.spots import bridges_geojson
    from src.storage.database import get_db

    box = _parse_bbox(bbox)
    return bridges_geojson(get_db(), box, jurisdiction)


@app.get("/map/water-bodies/{water_body_id}/features")
def get_map_water_body_features(water_body_id: str, user: dict = Depends(get_current_user)):
    """A lake's shoreline features as GeoJSON: inlets, outlets, islands, points
    and peninsulas, derived from its OHN outline. 404 for an unknown water body."""
    from src.services.water_bodies import water_body_features_geojson
    from src.storage.database import get_db

    out = water_body_features_geojson(get_db(), water_body_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"No water body with id {water_body_id!r}")
    return out


# --- Weed reports ---
#
# A weed edge the user saw and wrote down. Private: every route reads the user from
# the bearer token and nowhere else, and another user's report answers 404, exactly
# like one that does not exist. Nothing here returns a count, and nothing derives an
# edge from other data.


def _weed_db():
    from src.storage.database import get_db

    return get_db()


def _weed_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@app.get("/map/weed-reports/labels")
def get_weed_report_labels(user: dict = Depends(get_current_user)):
    """Every user-facing word and tolerance for weed reports, from
    src/services/weed_reports/labels.py. The web app hardcodes none of it."""
    from src.services.weed_reports.labels import labels_payload

    return labels_payload()


@app.get("/map/weed-reports/layer")
def get_weed_report_layer(
    water_body_id: str | None = None,
    bbox: str | None = None,
    user: dict = Depends(get_current_user),
):
    """The map layer: only the caller's own weed reports, as GeoJSON.

    Each feature is labelled "reported, not verified" and dated. `water_body_id` and
    `bbox` (min_lon,min_lat,max_lon,max_lat) narrow it. `meta` has no counts.
    """
    from src.services.weed_reports import weed_reports_geojson

    return weed_reports_geojson(_weed_db(), user["id"], water_body_id, _parse_bbox(bbox))


@app.get("/map/weed-reports")
def list_weed_report_routes(
    water_body_id: str | None = None,
    bbox: str | None = None,
    user: dict = Depends(get_current_user),
):
    """The caller's own weed reports, newest sighting first, optionally for one water
    body and/or a box."""
    from src.services.weed_reports import list_weed_reports

    return list_weed_reports(_weed_db(), user["id"], water_body_id, _parse_bbox(bbox))


@app.post("/map/weed-reports", status_code=201)
def post_weed_report(body: WeedReportCreate, user: dict = Depends(get_current_user)):
    """Report a weed edge: a point or a line on a water body, and the date you saw it.

    404 for an unknown water body; 422 when the position is not on or near the water
    body's drawn shape, or the water body has no shape on file to check against."""
    from src.services.weed_reports import WeedReportError, create_weed_report

    try:
        return create_weed_report(_weed_db(), user["id"], body)
    except WeedReportError as exc:
        raise _weed_error(exc) from exc


@app.get("/map/weed-reports/{report_id}")
def get_weed_report_detail(report_id: int, user: dict = Depends(get_current_user)):
    """One of the caller's weed reports. 404 if it is not theirs."""
    from src.services.weed_reports import weed_report_payload

    payload = weed_report_payload(_weed_db(), user["id"], report_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No such weed report")
    return payload


@app.patch("/map/weed-reports/{report_id}")
def patch_weed_report(
    report_id: int, body: WeedReportUpdate, user: dict = Depends(get_current_user)
):
    """Change the position, date or note of one of the caller's reports. A new position is
    checked against the report's water body again. 404 if it is not theirs."""
    from src.services.weed_reports import WeedReportError, update_weed_report

    fields = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        payload = update_weed_report(_weed_db(), user["id"], report_id, fields)
    except WeedReportError as exc:
        raise _weed_error(exc) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="No such weed report")
    return payload


@app.delete("/map/weed-reports/{report_id}")
def delete_weed_report_endpoint(report_id: int, user: dict = Depends(get_current_user)):
    """Delete one of the caller's weed reports. 404 if it is not theirs."""
    from src.storage.weed_reports import delete_weed_report

    if not delete_weed_report(_weed_db(), user["id"], report_id):
        raise HTTPException(status_code=404, detail="No such weed report")
    return {"deleted": report_id}


@app.get("/map/my-stops")
def get_my_stops(user: dict = Depends(get_current_user)):
    """Return all of the user's logged stops with coordinates for personal map mode."""
    import json

    from src.storage.database import get_db

    db = get_db()
    rows = list(
        db.execute(
            """
        SELECT st.id, st.lat, st.lng, st.photo_lat, st.photo_lng,
               st.location_name, st.location_text,
               st.species_caught, st.was_productive,
               st.technique, st.gear,
               s.date, s.date_approx,
               sc.air_temp_c, sc.pressure_hpa, sc.anomaly_flag
        FROM stops st
        JOIN sessions s ON st.session_id = s.id
        LEFT JOIN session_conditions sc ON sc.session_id = s.id
        WHERE st.user_id = ?
          AND (st.lat IS NOT NULL OR st.photo_lat IS NOT NULL)
        ORDER BY s.id DESC
    """,
            [user["id"]],
        ).fetchall()
    )

    stops = []
    for r in rows:
        lat = r[3] if r[3] is not None else r[1]  # photo_lat preferred
        lng = r[4] if r[4] is not None else r[2]  # photo_lng preferred
        if not lat:
            continue
        species = json.loads(r[7] or "[]")
        stops.append(
            {
                "stop_id": r[0],
                "lat": lat,
                "lng": lng,
                "location": r[5] or r[6] or "Unknown",
                "species": species,
                "productive": bool(r[8]),
                "technique": r[9],
                "gear": r[10],
                "date": r[11] or r[12] or "Unknown",
                "conditions": {
                    "air_temp_c": r[13],
                    "pressure_hpa": r[14],
                    "anomaly_flag": r[15],
                }
                if r[13]
                else None,
            }
        )

    return {"stops": stops, "count": len(stops)}


# --- Personal-map pins ---
#
# Every route takes the user from the bearer token and nowhere else. Another
# user's pin, stop or trip answers 404, exactly like one that does not exist,
# so a guessed id says nothing about whether it is in use.


def _pin_db():
    from src.storage.database import get_db

    return get_db()


@app.get("/pins/labels")
def get_pin_labels(user: dict = Depends(get_current_user)):
    """Constants and wording for the pin UI, from src/services/pins/labels.py."""
    from src.services.pins.labels import labels_payload

    return labels_payload()


@app.get("/pins/today")
def get_pins_today(
    sort: TodaySort = "match",
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    user: dict = Depends(get_current_user),
):
    """The Today view: the caller's own pins in the order they chose, each with a reason.

    sort is match, distance, last_visited or favourites. distance needs lat and
    lng (where the angler is); for the other keys they only add distance_km.
    Only sort=match looks up today's conditions, so only its rows carry a
    match. Another angler's pins and trips can never change the result.
    """
    from src.services.pins.today import today_view

    try:
        return today_view(_pin_db(), user["id"], sort, lat, lng)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/pins")
def get_pins(user: dict = Depends(get_current_user)):
    """The Personal map: the user's pins, and their located stops not yet pinned."""
    from src.services.pins import map_payload

    return map_payload(_pin_db(), user["id"])


@app.post("/pins", status_code=201)
def post_pin(body: PinCreate, user: dict = Depends(get_current_user)):
    """Create a pin. from_stop_id is "Pin this spot" and links that stop."""
    from src.services.pins import create_pin, pin_detail

    db = _pin_db()
    created = create_pin(
        db,
        user["id"],
        body.name,
        body.lat,
        body.lng,
        notes=body.notes,
        is_favourite=body.is_favourite,
        from_stop_id=body.from_stop_id,
    )
    if created is None:
        raise HTTPException(status_code=404, detail="No such stop")
    return {
        **pin_detail(db, user["id"], created["pin_id"]),
        "inferred_stop_ids": created["inferred_stop_ids"],
    }


@app.get("/pins/{pin_id}")
def get_pin_detail(pin_id: int, user: dict = Depends(get_current_user)):
    """One pin: its facts, linked trips, and recalled records (marked recalled)."""
    from src.services.pins import pin_detail

    detail = pin_detail(_pin_db(), user["id"], pin_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such pin")
    return detail


@app.get("/pins/{pin_id}/conditions")
def get_pin_conditions(pin_id: int, user: dict = Depends(get_current_user)):
    """The pin's conditions panel: one row per fact, each with source and age.

    Rows carry value, unit, source, station, distance_km, observed_at, age_s and
    stale, or an empty_reason saying why there is no value. Wording for every
    field and reason is in GET /pins/labels.
    """
    from src.services.pins.conditions import pin_conditions

    rows = pin_conditions(_pin_db(), user["id"], pin_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="No such pin")
    return rows


@app.get("/pins/{pin_id}/match")
def get_pin_match(pin_id: int, user: dict = Depends(get_current_user)):
    """How today's conditions compare with the caller's own best-rated trips at this pin.

    Built only from the caller's rated trips here. With fewer than the minimum
    (see GET /pins/labels) the answer has status "hidden" and shows nothing but
    the counts: the UI shows the conditions panel alone.
    """
    from src.services.pins.match import pin_match

    match = pin_match(_pin_db(), user["id"], pin_id)
    if match is None:
        raise HTTPException(status_code=404, detail="No such pin")
    return match


@app.patch("/pins/{pin_id}")
def patch_pin(pin_id: int, body: PinUpdate, user: dict = Depends(get_current_user)):
    """Rename, edit notes, or set or clear the favourite flag."""
    from src.services.pins import pin_detail
    from src.storage.pins import update_pin

    db = _pin_db()
    if not update_pin(db, user["id"], pin_id, body.model_dump(exclude_unset=True)):
        raise HTTPException(status_code=404, detail="No such pin")
    return pin_detail(db, user["id"], pin_id)


@app.delete("/pins/{pin_id}")
def delete_pin_endpoint(pin_id: int, user: dict = Depends(get_current_user)):
    """Delete a pin. Its trips are unlinked and kept; its recalled records go with it."""
    from src.storage.pins import delete_pin

    if not delete_pin(_pin_db(), user["id"], pin_id):
        raise HTTPException(status_code=404, detail="No such pin")
    return {"deleted": pin_id}


@app.post("/pins/{pin_id}/merge")
def merge_pin(pin_id: int, body: PinMerge, user: dict = Depends(get_current_user)):
    """Merge this pin into into_pin_id: its trips and recalled records move there."""
    from src.services.pins import pin_detail
    from src.storage.pins import merge_pins

    db = _pin_db()
    if pin_id == body.into_pin_id:
        raise HTTPException(status_code=400, detail="A pin cannot be merged into itself")
    if not merge_pins(db, user["id"], pin_id, body.into_pin_id):
        raise HTTPException(status_code=404, detail="No such pin")
    return pin_detail(db, user["id"], body.into_pin_id)


@app.post("/pins/{pin_id}/links")
def link_stop_to_pin(pin_id: int, body: PinLinkCreate, user: dict = Depends(get_current_user)):
    """Link one of the user's stops to this pin by hand (a confirmed link)."""
    from src.storage.pins import link_stop

    link = link_stop(_pin_db(), user["id"], pin_id, body.stop_id, "confirmed")
    if link is None:
        raise HTTPException(status_code=404, detail="No such pin or stop")
    return link


@app.delete("/pins/{pin_id}/links/{stop_id}")
def unlink_stop_from_pin(pin_id: int, stop_id: int, user: dict = Depends(get_current_user)):
    """Undo a link. The stop and its trip are kept."""
    from src.storage.pins import unlink_stop

    if not unlink_stop(_pin_db(), user["id"], pin_id, stop_id):
        raise HTTPException(status_code=404, detail="No such link")
    return {"unlinked": stop_id}


@app.post("/pins/{pin_id}/recalled", status_code=201)
def add_recalled(pin_id: int, body: RecalledVisitCreate, user: dict = Depends(get_current_user)):
    """ "I've fished here before": recalled history, excluded from every statistic."""
    from src.storage.pins import add_recalled_visit

    recalled_id = add_recalled_visit(
        _pin_db(), user["id"], pin_id, body.date_approx, body.species, body.rating
    )
    if recalled_id is None:
        raise HTTPException(status_code=404, detail="No such pin")
    return {"id": recalled_id, "pin_id": pin_id, "provenance": "recalled"}


@app.delete("/pins/{pin_id}/recalled/{recalled_id}")
def delete_recalled(pin_id: int, recalled_id: int, user: dict = Depends(get_current_user)):
    from src.storage.pins import delete_recalled_visit

    if not delete_recalled_visit(_pin_db(), user["id"], pin_id, recalled_id):
        raise HTTPException(status_code=404, detail="No such recalled record")
    return {"deleted": recalled_id}


@app.patch("/sessions/{session_id}")
def patch_session(session_id: int, body: SessionUpdate, user: dict = Depends(get_current_user)):
    """Rate a trip, or set or correct its start and end times.

    A field left out is unchanged; null clears it. A single-pin trip's rating
    is also that pin's rating.
    """
    from datetime import datetime

    from src.storage.pins import get_session, update_session

    db = _pin_db()
    current = get_session(db, user["id"], session_id)
    if current is None:
        raise HTTPException(status_code=404, detail="No such trip")
    fields = body.model_dump(exclude_unset=True)
    for key in ("started_at", "ended_at"):
        if fields.get(key) is not None:
            fields[key] = fields[key].isoformat()
    start = fields.get("started_at", current["started_at"])
    end = fields.get("ended_at", current["ended_at"])
    if start and end:
        try:
            ends_first = datetime.fromisoformat(end) <= datetime.fromisoformat(start)
        except (TypeError, ValueError):
            ends_first = False  # a stored legacy value we cannot compare
        if ends_first:
            raise HTTPException(status_code=400, detail="ended_at must be after started_at")
    update_session(db, user["id"], session_id, fields)
    return get_session(db, user["id"], session_id)


@app.put("/sessions/{session_id}/pins/{pin_id}/rating")
def put_trip_pin_rating(
    session_id: int, pin_id: int, body: TripPinRating, user: dict = Depends(get_current_user)
):
    """Rate one pin of a trip that covered several. null clears it."""
    from src.storage.pins import set_trip_pin_rating

    if not set_trip_pin_rating(_pin_db(), user["id"], session_id, pin_id, body.rating):
        raise HTTPException(status_code=404, detail="That trip is not linked to that pin")
    return {"session_id": session_id, "pin_id": pin_id, "rating": body.rating}


# Serve React web app from /app — mount last so API routes take priority
_web_dist = os.path.join(os.path.dirname(__file__), "../../web/dist")
if os.path.exists(_web_dist):
    app.mount("/app", StaticFiles(directory=_web_dist, html=True), name="webapp")
