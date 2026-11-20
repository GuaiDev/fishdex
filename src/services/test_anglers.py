"""Local test anglers: fake accounts made by the real sign-up code, for trying the app.

`create_test_anglers` does not write users itself. It mints an invite code and redeems it
with canned answers through `redeem_invite_code`, the same function the sign-up route
calls, so a test angler is exactly as onboarded as a real one and a questionnaire change
that breaks sign-up breaks these too. Their usernames start with `test_`; the sign-up
route only accepts letters and numbers, so no real tester can ever hold such a name and
`reset_test_anglers` can delete by that prefix without touching a real account.
"""

from sqlite_utils import Database

from src.auth.auth import generate_invite_code, redeem_invite_code
from src.models.questionnaire import QuestionSet

TEST_USERNAME_PREFIX = "test_"
ADMIN_USER_ID = 1


def create_test_anglers(
    db: Database, questions: QuestionSet, canned: dict[str, dict]
) -> list[dict]:
    """Sign up one test angler per canned answer set. One result row per angler:
    {"kind", "username", "created", "user_id", "token", "error"}.

    An angler that already exists is left alone (`created` False); reset first to remake it.
    """
    results = []
    for kind, answers in canned.items():
        username = f"{TEST_USERNAME_PREFIX}{kind}"
        existing = db.execute(
            "SELECT id FROM users WHERE LOWER(username) = ?", [username]
        ).fetchone()
        if existing:
            results.append(
                {
                    "kind": kind,
                    "username": username,
                    "created": False,
                    "user_id": existing[0],
                    "token": None,
                    "error": None,
                }
            )
            continue
        code = generate_invite_code(db, note=f"test angler: {kind}")
        got = redeem_invite_code(
            db, code, username, f"Test {kind}", questions=questions, answers=answers
        )
        if not got["success"]:
            # Spend nothing on a failed sign-up: the code was minted for this one attempt.
            db.execute("DELETE FROM invite_codes WHERE code = ?", [code])
            db.conn.commit()
        results.append(
            {
                "kind": kind,
                "username": username,
                "created": got["success"],
                "user_id": got.get("user_id"),
                "token": got.get("token"),
                "error": None if got["success"] else (got.get("problems") or got["error"]),
            }
        )
    return results


def reset_test_anglers(db: Database) -> dict[str, int]:
    """Delete every `test_` angler and the rows that belong to them. Returns rows removed
    per table. Never touches user 1 or any user whose name lacks the prefix."""
    ids = [
        r[0]
        for r in db.execute(
            "SELECT id FROM users WHERE username LIKE ? ESCAPE '\\' AND id != ?",
            [TEST_USERNAME_PREFIX.replace("_", "\\_") + "%", ADMIN_USER_ID],
        ).fetchall()
    ]
    removed: dict[str, int] = {}
    if not ids:
        return removed
    marks = ",".join("?" * len(ids))
    with db.conn:
        for table in db.table_names():
            if table in ("users", "invite_codes") or table.startswith("sqlite_"):
                continue
            if "user_id" not in {c.name for c in db[table].columns}:
                continue
            n = db.execute(f"DELETE FROM {table} WHERE user_id IN ({marks})", ids).rowcount
            if n:
                removed[table] = n
        removed["invite_codes"] = db.execute(
            f"DELETE FROM invite_codes WHERE used_by IN ({marks})", ids
        ).rowcount
        removed["users"] = db.execute(f"DELETE FROM users WHERE id IN ({marks})", ids).rowcount
    return removed
