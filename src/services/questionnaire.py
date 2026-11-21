"""Loads the onboarding questions, and refuses placeholders unless told it is local.

The question list is a data file so the real design is a file swap. While that file
is a stand-in (`"stand_in": true`), no tester may see it: the loader raises unless the
caller allows it, and the API only allows it when FISHBOT_ALLOW_STAND_IN_QUESTIONS=1.
The result is that a server without the real questions cannot create an account at all,
which is the safe failure: no account without the questionnaire.
"""

import json
import os
from pathlib import Path

from src.models.questionnaire import QuestionSet

QUESTIONS_PATH = Path("data/questionnaire/questions.json")
CANNED_ANSWERS_PATH = Path("data/questionnaire/stand_in_test_anglers.json")
ALLOW_STAND_IN_ENV = "FISHBOT_ALLOW_STAND_IN_QUESTIONS"


class StandInQuestionsRefused(RuntimeError):
    """The question list is a placeholder and the caller did not say it is running locally."""


def stand_in_allowed_by_env() -> bool:
    return os.environ.get(ALLOW_STAND_IN_ENV, "").strip() == "1"


def load_question_set(path: Path | None = None, *, allow_stand_in: bool = False) -> QuestionSet:
    qs = QuestionSet.model_validate_json((path or QUESTIONS_PATH).read_text())
    if qs.stand_in and not allow_stand_in:
        raise StandInQuestionsRefused(
            "the onboarding questions are stand-ins and are not served to testers; "
            f"set {ALLOW_STAND_IN_ENV}=1 to run them locally"
        )
    return qs


def load_canned_answers(path: Path | None = None) -> dict[str, dict]:
    """Canned answers per test-angler type (regular, explorer, pond)."""
    return json.loads((path or CANNED_ANSWERS_PATH).read_text())["anglers"]
