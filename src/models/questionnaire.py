"""The onboarding questionnaire: a versioned list of questions and the rules for an answer.

The questions themselves are data (`data/questionnaire/questions.json`), not code, so
the real design replaces a file and changes no logic. Every question is required: a
set of answers is accepted whole or not at all, and there is no way to leave one out.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from src.models.jurisdiction import JurisdictionCode
from src.models.profile import Location


class QuestionOption(BaseModel):
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)


class Question(BaseModel):
    """One question. `id` is the key its answer is stored under."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["choice", "multi_choice", "home"]
    prompt: str = Field(min_length=1)
    options: list[QuestionOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def _options_fit_kind(self) -> Self:
        if self.kind == "home":
            if self.options:
                raise ValueError("a home question takes no options")
            return self
        values = [o.value for o in self.options]
        if len(values) < 2:
            raise ValueError(f"question '{self.id}' needs at least two options")
        if len(set(values)) != len(values):
            raise ValueError(f"question '{self.id}' repeats an option value")
        return self


class AnswerError(ValueError):
    """The answers do not satisfy the question set. `problems` maps question id to why."""

    def __init__(self, problems: dict[str, str]) -> None:
        self.problems = problems
        super().__init__("; ".join(f"{k}: {v}" for k, v in problems.items()))


class QuestionSet(BaseModel):
    version: str = Field(min_length=1)
    # True while the questions are placeholders. A stand-in set is refused by the loader
    # unless the caller says it is running locally (see src/services/questionnaire.py).
    stand_in: bool
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_unique_one_home(self) -> Self:
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique")
        if sum(q.kind == "home" for q in self.questions) > 1:
            raise ValueError("a question set can ask for home at most once")
        return self

    def validate_answers(self, answers: object) -> dict[str, Any]:
        """The cleaned answers, keyed by question id, or AnswerError naming every problem.

        All questions are required and unknown keys are refused, so a partial or
        padded submission never reaches storage.
        """
        if not isinstance(answers, dict):
            raise AnswerError({"answers": "must be an object keyed by question id"})
        problems: dict[str, str] = {}
        clean: dict[str, Any] = {}
        known = {q.id for q in self.questions}
        for key in answers:
            if key not in known:
                problems[str(key)] = "not a question in this questionnaire"
        for q in self.questions:
            if q.id not in answers or answers[q.id] in (None, "", [], {}):
                problems[q.id] = "required"
                continue
            try:
                clean[q.id] = _clean_answer(q, answers[q.id])
            except ValueError as e:
                problems[q.id] = str(e)
        if problems:
            raise AnswerError(problems)
        return clean

    def home_answer(self, clean: dict[str, Any]) -> Location | None:
        """The Location from the home question's cleaned answer, if the set asks for one."""
        for q in self.questions:
            if q.kind == "home":
                h = clean[q.id]
                return Location(name=h["name"], lat=h["lat"], lng=h["lng"])
        return None

    def home_jurisdiction(self, clean: dict[str, Any]) -> str | None:
        for q in self.questions:
            if q.kind == "home":
                return clean[q.id]["jurisdiction"]
        return None


class _HomeAnswer(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    jurisdiction: JurisdictionCode


def _clean_answer(q: Question, raw: Any) -> Any:
    if q.kind == "home":
        try:
            return _HomeAnswer.model_validate(raw).model_dump()
        except ValidationError as e:
            raise ValueError("home needs name, lat, lng and a jurisdiction code") from e
    allowed = {o.value for o in q.options}
    if q.kind == "choice":
        if not isinstance(raw, str) or raw not in allowed:
            raise ValueError("not one of the offered options")
        return raw
    if (
        not isinstance(raw, list)
        or not raw
        or len(set(map(str, raw))) != len(raw)
        or not all(isinstance(v, str) and v in allowed for v in raw)
    ):
        raise ValueError("must be a list of distinct offered options")
    return list(raw)
