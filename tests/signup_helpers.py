"""Create a test user the way real sign-up does: through redeem_invite_code, with answers."""

from sqlite_utils import Database

from src.auth.auth import redeem_invite_code
from src.services.questionnaire import load_canned_answers, load_question_set


def redeem_test_user(db: Database, code: str, username: str, display_name: str = "") -> dict:
    return redeem_invite_code(
        db,
        code,
        username,
        display_name,
        questions=load_question_set(allow_stand_in=True),
        answers=load_canned_answers()["regular"],
    )
