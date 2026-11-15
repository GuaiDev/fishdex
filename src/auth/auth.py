"""
Simple invite-code auth for FishBot beta.
No email, no OAuth. Generate codes, friends enter them, they get accounts.
"""
import os
import secrets
from datetime import datetime, timedelta

from sqlite_utils import Database


def generate_invite_code(db: Database, created_by: int = 1, note: str = "") -> str:
    """Generate a new invite code. Returns the code string."""
    code = secrets.token_urlsafe(8).upper()
    db["invite_codes"].insert({
        "code": code,
        "created_by": created_by,
        "note": note,
        "is_active": 1,
    })
    db.conn.commit()
    return code


def redeem_invite_code(
    db: Database, code: str, username: str, display_name: str = ""
) -> dict:
    """
    Redeem an invite code and create a user account.
    Returns {"success": True, "token": "...", "user_id": N}
    or {"success": False, "error": "..."}
    """
    code = code.strip().upper()

    invite = next(
        db["invite_codes"].rows_where(
            "code = ? AND is_active = 1 AND used_by IS NULL", [code]
        ),
        None,
    )
    if not invite:
        return {"success": False, "error": "Invalid or already used invite code."}

    existing = next(
        db["users"].rows_where("LOWER(username) = ?", [username.lower()]),
        None,
    )
    if existing:
        return {"success": False, "error": "Username already taken."}

    user_id = db["users"].insert({
        "username": username.lower(),
        "display_name": display_name or username,
        "role": "beta",
        "daily_message_limit": 50,
    }).last_pk

    db["invite_codes"].update(invite["id"], {
        "used_by": user_id,
        "used_at": datetime.now().isoformat(),
        "is_active": 0,
    })
    db.conn.commit()

    token = _create_token(db, user_id)
    return {"success": True, "token": token, "user_id": user_id}


def _create_token(db: Database, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=90)).isoformat()
    db["user_sessions"].insert({
        "user_id": user_id,
        "token": token,
        "expires_at": expires,
        "last_used_at": datetime.now().isoformat(),
    })
    db.conn.commit()
    return token


def get_user_from_token(db: Database, token: str) -> dict | None:
    """
    Validate a session token and return the user dict.
    Returns None if token is invalid or expired.
    """
    if not token:
        return None

    session = next(
        db["user_sessions"].rows_where("token = ?", [token]),
        None,
    )
    if not session:
        return None

    if session.get("expires_at"):
        try:
            expires = datetime.fromisoformat(session["expires_at"])
            if datetime.now() > expires:
                return None
        except Exception:
            pass

    user = next(
        db["users"].rows_where("id = ? AND is_active = 1", [session["user_id"]]),
        None,
    )
    if not user:
        return None

    try:
        db["user_sessions"].update(session["id"], {
            "last_used_at": datetime.now().isoformat()
        })
        db["users"].update(user["id"], {
            "last_seen_at": datetime.now().isoformat()
        })
        db.conn.commit()
    except Exception:
        pass

    return user


def check_rate_limit(db: Database, user_id: int, limit: int = 50) -> dict:
    """
    Check if user has exceeded daily message limit.
    Returns {"allowed": bool, "used": int, "limit": int}
    """
    today = datetime.now().strftime("%Y-%m-%d")
    row = next(
        db["daily_usage"].rows_where(
            "user_id = ? AND date = ?", [user_id, today]
        ),
        None,
    )
    used = row["message_count"] if row else 0
    return {"allowed": used < limit, "used": used, "limit": limit}


# daily_usage row that holds the whole server's spend for the day (no real user has id 0).
GLOBAL_USAGE_USER_ID = 0


def global_daily_budget() -> int | None:
    """FISHBOT_DAILY_MODEL_CALLS: the most spend units the whole server allows per day.

    Unset, empty or not a positive integer means no global switch.
    """
    raw = os.environ.get("FISHBOT_DAILY_MODEL_CALLS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _reserve(db: Database, user_id: int, today: str, cost: int, limit: int) -> bool:
    """Add `cost` to a day's counter if it still fits under `limit`, in one statement.

    The check and the write are the same SQL statement, so two requests cannot both
    pass the check and then both write (the old check-then-increment race).
    """
    if cost > limit:
        return False
    cur = db.execute(
        """
        INSERT INTO daily_usage (user_id, date, message_count)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, date)
        DO UPDATE SET message_count = message_count + ?
        WHERE message_count + ? <= ?
        RETURNING message_count
        """,
        [user_id, today, cost, cost, cost, limit],
    )
    row = cur.fetchone()
    db.conn.commit()
    return row is not None


def reserve_usage(db: Database, user_id: int, limit: int, cost: int = 1) -> dict:
    """Reserve `cost` units of today's model spend BEFORE the model is called.

    The units are spent for good: a request that fails afterwards is not refunded, because
    the model calls it made were paid for either way. Only a refused reservation costs nothing.

    Returns {"allowed": bool, "reason": None | "user" | "global", "limit": int}.
    "user" means the person's own daily limit; "global" means the server-wide
    FISHBOT_DAILY_MODEL_CALLS budget (the caller answers 503, not 429).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if not _reserve(db, user_id, today, cost, limit):
        return {"allowed": False, "reason": "user", "limit": limit}

    budget = global_daily_budget()
    if budget is not None and not _reserve(db, GLOBAL_USAGE_USER_ID, today, cost, budget):
        # The person had room; the server did not. Give their units back.
        db.execute(
            "UPDATE daily_usage SET message_count = message_count - ? "
            "WHERE user_id = ? AND date = ?",
            [cost, user_id, today],
        )
        db.conn.commit()
        return {"allowed": False, "reason": "global", "limit": budget}
    return {"allowed": True, "reason": None, "limit": limit}
