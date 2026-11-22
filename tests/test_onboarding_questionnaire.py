"""Onboarding questionnaire: no account without an answer to every question.

Every database is temporary; the shipped stand-in questions are the realistic input.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import src.storage.database as database
from src.api.main import app
from src.auth.auth import generate_invite_code, redeem_invite_code
from src.cli.main import app as cli_app
from src.models.questionnaire import AnswerError, Question, QuestionSet
from src.services import test_anglers as test_anglers_mod
from src.services.questionnaire import (
    ALLOW_STAND_IN_ENV,
    StandInQuestionsRefused,
    load_canned_answers,
    load_question_set,
)
from src.services.test_anglers import create_test_anglers, reset_test_anglers
from src.storage import questionnaire as store
from src.storage.profile import get_user_home

HOME = {"name": "Oakville, ON", "lat": 43.65, "lng": -79.38, "jurisdiction": "CA-ON"}


@pytest.fixture
def questions():
    return load_question_set(allow_stand_in=True)


@pytest.fixture
def canned():
    return load_canned_answers()


def _opts(*values):
    return [{"value": v, "label": v.upper()} for v in values]


def _set(*qs, stand_in=False):
    return QuestionSet(version="t1", stand_in=stand_in, questions=list(qs))


def _choice(qid="water", values=("a", "b")):
    return {"id": qid, "kind": "choice", "prompt": "?", "options": _opts(*values)}


# ── model ────────────────────────────────────────────────────────────────────


def test_question_needs_two_distinct_options():
    with pytest.raises(ValueError):
        Question(**_choice(values=("a",)))
    with pytest.raises(ValueError):
        Question(**_choice(values=("a", "a")))


def test_home_question_takes_no_options_and_only_one_home_per_set():
    with pytest.raises(ValueError):
        Question(id="home", kind="home", prompt="?", options=_opts("a", "b"))
    home = {"id": "home", "kind": "home", "prompt": "?"}
    with pytest.raises(ValueError):
        _set(home, {**home, "id": "home2"})


def test_duplicate_question_ids_are_refused():
    with pytest.raises(ValueError):
        _set(_choice(), _choice())


def test_shipped_questions_load_and_are_marked_stand_in(questions):
    assert questions.stand_in is True
    kinds = {q.kind for q in questions.questions}
    assert kinds == {"home", "choice", "multi_choice"}


def test_complete_answers_are_cleaned(questions, canned):
    clean = questions.validate_answers(canned["regular"])
    assert set(clean) == {q.id for q in questions.questions}


@pytest.mark.parametrize("missing", ["home", "water_type", "outing_style", "species_interest"])
def test_every_question_is_required(questions, canned, missing):
    answers = {k: v for k, v in canned["regular"].items() if k != missing}
    with pytest.raises(AnswerError) as e:
        questions.validate_answers(answers)
    assert e.value.problems == {missing: "required"}


@pytest.mark.parametrize("empty", ["", None, [], {}])
def test_an_empty_answer_counts_as_unanswered(questions, canned, empty):
    with pytest.raises(AnswerError) as e:
        questions.validate_answers({**canned["regular"], "water_type": empty})
    assert "water_type" in e.value.problems


def test_unknown_and_invalid_answers_name_each_problem(questions, canned):
    bad = {
        **canned["regular"],
        "water_type": "the moon",
        "species_interest": ["gamefish", "gamefish"],
        "home": {**HOME, "lat": 123},
        "extra": "x",
    }
    with pytest.raises(AnswerError) as e:
        questions.validate_answers(bad)
    assert set(e.value.problems) == {"water_type", "species_interest", "home", "extra"}


def test_answers_must_be_an_object(questions):
    for junk in (None, [], "answers"):
        with pytest.raises(AnswerError):
            questions.validate_answers(junk)


# ── loader: stand-ins never reach a tester ───────────────────────────────────


def test_stand_in_questions_are_refused_unless_allowed():
    with pytest.raises(StandInQuestionsRefused):
        load_question_set()


def test_a_real_question_set_loads_without_the_flag(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(_set(_choice()).model_dump_json())
    assert load_question_set(path).stand_in is False


# ── storage ──────────────────────────────────────────────────────────────────


def test_migration_creates_the_answers_table(db):
    assert "questionnaire_answers" in db.table_names()
    assert 33 in database.applied_migrations(db)


def test_answers_are_stored_per_user(db):
    store.save_answers(db, 1, "v1", {"water_type": "ponds", "species_interest": ["a", "b"]})
    store.save_answers(db, 2, "v1", {"water_type": "rivers"})
    assert store.get_answers(db, 1) == {"water_type": "ponds", "species_interest": ["a", "b"]}
    assert store.get_answers(db, 2) == {"water_type": "rivers"}
    assert store.get_answers(db, 3) == {}


def test_a_second_save_replaces_the_first(db):
    store.save_answers(db, 1, "v1", {"water_type": "ponds"})
    store.save_answers(db, 1, "v1", {"water_type": "rivers"})
    assert store.get_answers(db, 1, "v1") == {"water_type": "rivers"}


# ── the one account path ─────────────────────────────────────────────────────


def _user_count(db):
    return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def test_signup_stores_answers_and_home_with_the_account(db, questions, canned):
    code = generate_invite_code(db)
    got = redeem_invite_code(db, code, "ann", questions=questions, answers=canned["pond"])
    assert got["success"], got
    uid = got["user_id"]
    assert store.get_answers(db, uid)["water_type"] == "ponds"
    home, jurisdiction = get_user_home(db, uid)
    assert (home.name, jurisdiction) == ("Burlington, ON", "CA-ON")


def test_incomplete_answers_make_no_account_and_keep_the_invite(db, questions, canned):
    before = _user_count(db)
    code = generate_invite_code(db)
    answers = {k: v for k, v in canned["regular"].items() if k != "species_interest"}
    got = redeem_invite_code(db, code, "ann", questions=questions, answers=answers)
    assert got["success"] is False
    assert got["problems"] == {"species_interest": "required"}
    assert _user_count(db) == before
    again = redeem_invite_code(db, code, "ann", questions=questions, answers=canned["regular"])
    assert again["success"], "a refused attempt must not spend the invite code"


def test_answers_are_a_required_argument():
    with pytest.raises(TypeError):
        redeem_invite_code(None, "CODE", "ann")


def test_a_failure_after_the_user_row_leaves_no_account(db, questions, canned, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("src.auth.auth.answer_store.save_answers", boom)
    before = _user_count(db)
    code = generate_invite_code(db)
    with pytest.raises(RuntimeError):
        redeem_invite_code(db, code, "ann", questions=questions, answers=canned["regular"])
    assert _user_count(db) == before
    row = db.execute(
        "SELECT is_active, used_by FROM invite_codes WHERE code = ?", [code]
    ).fetchone()
    assert tuple(row) == (1, None)


def test_only_the_auth_module_inserts_users():
    """No hidden account path: outside auth.py and the schema file, nothing writes a user row."""
    root = Path(__file__).resolve().parents[1] / "src"
    allowed = {root / "auth" / "auth.py", root / "storage" / "database.py"}
    pattern = re.compile(
        r"""\[["']users["']\]\s*\.\s*(insert|upsert)|INSERT\s+(OR\s+\w+\s+)?INTO\s+users""", re.I
    )
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p not in allowed and pattern.search(p.read_text())
    ]
    assert offenders == []


def test_signup_is_the_only_route_that_makes_accounts():
    names = {r.path for r in app.routes if hasattr(r, "path")}
    suspicious = {p for p in names if re.search(r"signup|register|skip|account", p, re.I)}
    assert suspicious == {"/auth/signup"}


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, migrated_template_db):
    import shutil

    shutil.copy(migrated_template_db, tmp_path / "api.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(app, "dependency_overrides", {})
    monkeypatch.setenv(ALLOW_STAND_IN_ENV, "1")
    return TestClient(app)


def _signup(client, code, username, answers, **extra):
    body = {"code": code, "username": username, **extra}
    if answers is not None:
        body["answers"] = answers
    return client.post("/auth/signup", json=body)


def test_questions_route_serves_the_list(client):
    r = client.get("/onboarding/questions")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "stand-in-1"
    assert [q["id"] for q in body["questions"]][0] == "home"
    assert "stand_in" not in body


def test_stand_in_questions_are_never_served_without_the_local_flag(client, monkeypatch, canned):
    monkeypatch.delenv(ALLOW_STAND_IN_ENV)
    assert client.get("/onboarding/questions").status_code == 503
    code = generate_invite_code(database.get_db())
    r = _signup(client, code, "ann", canned["regular"])
    assert r.status_code == 503
    assert _user_count(database.get_db()) == 1  # nobody signed up; only the seeded admin


def test_signup_without_answers_is_422_and_makes_no_account(client):
    db = database.get_db()
    code = generate_invite_code(db)
    for answers in (None, {}):
        r = _signup(client, code, "ann", answers)
        assert r.status_code == 422, r.text
    assert _user_count(db) == 1


def test_signup_with_one_answer_missing_names_it(client, canned):
    db = database.get_db()
    code = generate_invite_code(db)
    partial = {k: v for k, v in canned["explorer"].items() if k != "outing_style"}
    r = _signup(client, code, "bob", partial)
    assert r.status_code == 422
    assert r.json()["detail"]["problems"] == {"outing_style": "required"}
    assert _user_count(db) == 1


def test_complete_signup_creates_the_account_and_the_token_works(client, canned):
    db = database.get_db()
    code = generate_invite_code(db)
    r = _signup(client, code, "cleo", canned["explorer"], display_name="Cleo")
    assert r.status_code == 200, r.text
    got = r.json()
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {got['token']}"})
    assert me.status_code == 200 and me.json()["username"] == "cleo"
    assert store.get_answers(db, got["user_id"])["water_type"] == "streams"
    assert get_user_home(db, got["user_id"])[0].name == "Guelph, ON"


# ── test anglers ─────────────────────────────────────────────────────────────


def test_test_anglers_go_through_the_real_signup_function(db, questions, canned, monkeypatch):
    calls = []
    real = test_anglers_mod.redeem_invite_code

    def spy(*a, **k):
        calls.append(a[2])
        return real(*a, **k)

    monkeypatch.setattr(test_anglers_mod, "redeem_invite_code", spy)
    results = create_test_anglers(db, questions, canned)
    assert calls == ["test_regular", "test_explorer", "test_pond"]
    assert all(r["created"] for r in results)
    for r in results:
        assert store.get_answers(db, r["user_id"]) != {}
        assert get_user_home(db, r["user_id"])[0] is not None


def test_creating_twice_leaves_the_existing_angler_alone(db, questions, canned):
    first = create_test_anglers(db, questions, canned)
    second = create_test_anglers(db, questions, canned)
    assert not any(r["created"] for r in second)
    assert [r["user_id"] for r in first] == [r["user_id"] for r in second]


def test_canned_answers_that_do_not_fit_make_no_account_and_leave_no_invite(db, questions):
    before = _user_count(db)
    invites = db["invite_codes"].count
    results = create_test_anglers(db, questions, {"broken": {"water_type": "ponds"}})
    assert results[0]["created"] is False and results[0]["error"]
    assert _user_count(db) == before
    assert db["invite_codes"].count == invites


def test_reset_removes_only_test_anglers_and_their_rows(db, questions, canned):
    real = redeem_invite_code(
        db, generate_invite_code(db), "testregular", questions=questions, answers=canned["regular"]
    )
    made = create_test_anglers(db, questions, canned)
    ids = [r["user_id"] for r in made]
    removed = reset_test_anglers(db)
    assert removed["users"] == 3
    assert removed["questionnaire_answers"] == len(questions.questions) * 3
    for uid in ids:
        assert db.execute("SELECT 1 FROM users WHERE id = ?", [uid]).fetchone() is None
    assert (
        db.execute("SELECT 1 FROM user_sessions WHERE user_id IN (?, ?, ?)", ids).fetchone() is None
    )
    # a real tester whose name merely resembles a test angler, and the admin, survive
    assert db.execute("SELECT 1 FROM users WHERE id = ?", [real["user_id"]]).fetchone()
    assert store.get_answers(db, real["user_id"]) != {}
    assert db.execute("SELECT 1 FROM users WHERE id = 1").fetchone()
    assert reset_test_anglers(db) == {}


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_creates_then_resets_test_anglers(tmp_path, monkeypatch, migrated_template_db):
    import shutil

    shutil.copy(migrated_template_db, tmp_path / "cli.db")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "cli.db")
    runner = CliRunner()
    made = runner.invoke(cli_app, ["create-test-anglers"])
    assert made.exit_code == 0, made.output
    for name in ("test_regular", "test_explorer", "test_pond"):
        assert name in made.output
    db = database.get_db()
    assert _user_count(db) == 4
    gone = runner.invoke(cli_app, ["reset-test-anglers"])
    assert gone.exit_code == 0, gone.output
    assert _user_count(database.get_db()) == 1
