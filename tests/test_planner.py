"""Study-plan generation: JSON validation + retries, deterministic rules, persistence."""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import func, select

from app.db.models import Task
from app.schemas.planner import LLMPlanTask, PlanRequest
from app.services.fakes import FakeChatModel, ScriptedChatModel
from app.services.planner_service import PlanWindowError, normalise_plan, plan_window
from app.services.time_utils import week_start
from tests.conftest import auth

A, B = auth("test-user-a"), auth("test-user-b")
UTC_TZ = ZoneInfo("UTC")


def session(day: date, hour: int = 18, minutes: int = 60, title: str = "Study") -> dict:
    return {
        "title": title,
        "notes": "Work through the chapter.",
        "due_at": f"{day.isoformat()}T{hour:02d}:00",
        "est_minutes": minutes,
    }


def plan_json(*sessions: dict) -> str:
    return json.dumps({"tasks": list(sessions)})


def today() -> date:
    return datetime.now(UTC).date()


# --------------------------------------------------------------------------- pure rules


def _tasks(*items):
    return [LLMPlanTask.model_validate(i) for i in items]


def test_normalise_moves_past_and_out_of_window_sessions_inside():
    tz = UTC_TZ
    earliest = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # now, Monday noon
    latest = datetime(2026, 10, 25, 23, 59, tzinfo=UTC)
    kept, notes = normalise_plan(
        _tasks(
            session(date(2026, 10, 1), 18),  # before the window -> first day, same time
            session(date(2026, 10, 5), 9),  # earlier today (past) -> tomorrow 09:00
            session(date(2026, 11, 20), 19),  # after the end -> last day 19:00
            session(date(2026, 10, 14), 18),  # fine
        ),
        earliest=earliest,
        latest=latest,
        tz=tz,
        weekly_budget_minutes=600,
    )
    dues = [k.due_at for k in kept]
    assert dues == [
        datetime(2026, 10, 5, 18, 0, tzinfo=UTC),
        datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 19, 0, tzinfo=UTC),
    ]
    assert all(earliest <= d <= latest for d in dues)
    assert any("Moved 3 session(s)" in n for n in notes)


def test_normalise_enforces_weekly_budget_by_moving_then_dropping():
    earliest = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # Monday
    latest = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)  # two weeks
    kept, notes = normalise_plan(
        _tasks(*[session(date(2026, 10, 5 + i), 18, 60, f"S{i}") for i in range(5)]),
        earliest=earliest,
        latest=latest,
        tz=UTC_TZ,
        weekly_budget_minutes=120,  # 2 hours a week
    )
    per_week: dict[date, int] = {}
    for k in kept:
        per_week[week_start(k.due_at, UTC_TZ)] = per_week.get(week_start(k.due_at, UTC_TZ), 0) + 60
    assert all(minutes <= 120 for minutes in per_week.values())
    assert len(kept) == 4  # 2 in week 1, 2 moved to week 2, 1 dropped
    assert any("to a later week" in n for n in notes)
    assert any("Dropped 1 session(s)" in n for n in notes)


def test_normalise_shortens_sessions_longer_than_the_budget():
    earliest = datetime(2026, 10, 5, tzinfo=UTC)
    kept, notes = normalise_plan(
        _tasks(session(date(2026, 10, 6), 18, 300)),
        earliest=earliest,
        latest=earliest + timedelta(days=13),
        tz=UTC_TZ,
        weekly_budget_minutes=90,
    )
    assert kept[0].est_minutes == 90
    assert any("Shortened 1" in n for n in notes)


def test_normalise_keeps_local_time_of_day():
    ist = ZoneInfo("Asia/Kolkata")
    earliest = datetime(2026, 10, 5, 0, 0, tzinfo=ist).astimezone(UTC)
    kept, _ = normalise_plan(
        _tasks(session(date(2026, 10, 7), 18)),
        earliest=earliest,
        latest=earliest + timedelta(days=6),
        tz=ist,
        weekly_budget_minutes=300,
    )
    assert kept[0].due_at.astimezone(ist).hour == 18
    assert kept[0].due_at == datetime(2026, 10, 7, 12, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- window rules


class _Goal:
    def __init__(self, target_date=None):
        self.target_date = target_date


NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def test_window_defaults_and_errors():
    start, end, earliest, _ = plan_window(
        _Goal(date(2026, 11, 30)), PlanRequest(hours_per_week=6), NOW, UTC_TZ
    )
    assert (start, end, earliest) == (date(2026, 10, 5), date(2026, 11, 30), NOW)

    # A start date in the past is moved to today.
    start, *_ = plan_window(
        _Goal(),
        PlanRequest(hours_per_week=6, start_date="2026-09-01", end_date="2026-10-20"),
        NOW,
        UTC_TZ,
    )
    assert start == date(2026, 10, 5)

    for goal, req, msg in [
        (_Goal(), PlanRequest(hours_per_week=6), "end_date is required"),
        (_Goal(date(2026, 9, 1)), PlanRequest(hours_per_week=6), "end today or later"),
        (
            _Goal(),
            PlanRequest(hours_per_week=6, end_date="2028-01-01"),
            "at most 366 days",
        ),
    ]:
        with pytest.raises(PlanWindowError, match=msg):
            plan_window(goal, req, NOW, UTC_TZ)


def test_plan_request_validation(db_client):
    goal = db_client.post("/goals", json={"title": "g"}, headers=A).json()
    for body in (
        {"hours_per_week": 0, "end_date": "2026-12-01"},
        {"hours_per_week": 100, "end_date": "2026-12-01"},
        {"hours_per_week": 5, "start_date": "2026-12-10", "end_date": "2026-12-01"},
    ):
        assert db_client.post(f"/goals/{goal['id']}/plan", json=body, headers=A).status_code == 422
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 5}, headers=A)
    assert resp.status_code == 422
    assert "end_date is required" in resp.json()["error"]["message"]


# --------------------------------------------------------------------------- API + LLM


@pytest.fixture
def goal(db_client):
    target = today() + timedelta(days=27)
    return db_client.post(
        "/goals",
        json={"title": "Finish ML course", "target_date": target.isoformat()},
        headers=A,
    ).json()


def use_model(db_app, replies: list[str]) -> ScriptedChatModel:
    model = ScriptedChatModel(replies=replies)
    model.prompts = []
    db_app.state.services.planner_model = model
    return model


def task_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Task))


def test_valid_plan_creates_dated_tasks_within_budget(db_app, db_client, db_session, goal):
    d = today() + timedelta(days=1)
    model = use_model(
        db_app,
        [plan_json(*[session(d + timedelta(days=i * 2), 18, 90, f"Topic {i}") for i in range(8)])],
    )
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 3}, headers=A)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(model.prompts) == 1

    tasks = body["tasks"]
    assert tasks and all(t["goal_id"] == goal["id"] and t["status"] == "todo" for t in tasks)
    end = datetime.fromisoformat(goal["target_date"]).date()
    per_week: dict[date, int] = {}
    for t in tasks:
        due = datetime.fromisoformat(t["due_at"])
        assert datetime.now(UTC) - timedelta(minutes=1) <= due
        assert due.date() <= end
        per_week[week_start(due, UTC_TZ)] = (
            per_week.get(week_start(due, UTC_TZ), 0) + t["est_minutes"]
        )
    assert max(per_week.values()) <= 180
    assert body["total_minutes"] == sum(t["est_minutes"] for t in tasks)
    assert task_count(db_session) == len(tasks)
    progress = db_client.get(f"/goals/{goal['id']}", headers=A).json()["progress"]
    assert progress["total"] == len(tasks)


def test_invalid_then_valid_json_is_retried(db_app, db_client, db_session, goal):
    good = plan_json(session(today() + timedelta(days=2)))
    model = use_model(db_app, ["Sure! Here is your plan: tasks go here", good])
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 4}, headers=A)
    assert resp.status_code == 201
    assert len(model.prompts) == 2
    retry_prompt = str(model.prompts[1][-1].content)
    assert "That output was invalid" in retry_prompt
    assert task_count(db_session) == 1


def test_schema_errors_are_fed_back(db_app, db_client, goal):
    bad = json.dumps({"tasks": [{"title": "", "due_at": "not a date", "est_minutes": 5}]})
    model = use_model(db_app, [bad, plan_json(session(today() + timedelta(days=3)))])
    assert (
        db_client.post(
            f"/goals/{goal['id']}/plan", json={"hours_per_week": 4}, headers=A
        ).status_code
        == 201
    )
    feedback = str(model.prompts[1][-1].content)
    assert "tasks.0.title" in feedback and "tasks.0.due_at" in feedback


def test_always_invalid_fails_readably_after_three_attempts(db_app, db_client, db_session, goal):
    model = use_model(db_app, ['{"tasks": []}', "not json", '{"plan": "nope"}'])
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 4}, headers=A)
    assert resp.status_code == 502
    assert resp.json()["error"] == {
        "code": "upstream_error",
        "message": "The assistant could not produce a valid study plan after 3 attempts. "
        "Please try again.",
    }
    assert len(model.prompts) == 3
    assert task_count(db_session) == 0


def test_code_fenced_json_is_accepted(db_app, db_client, goal):
    use_model(db_app, ["```json\n" + plan_json(session(today() + timedelta(days=2))) + "\n```"])
    assert (
        db_client.post(
            f"/goals/{goal['id']}/plan", json={"hours_per_week": 2}, headers=A
        ).status_code
        == 201
    )


def test_remembered_and_request_preferences_reach_the_prompt(db_app, db_client, goal):
    # The fake extractor stores this line as a memory (runs as a background task).
    db_client.post("/chat", json={"message": "I study best after 6 pm"}, headers=A)
    model = use_model(db_app, [plan_json(session(today() + timedelta(days=2)))])
    body = db_client.post(
        f"/goals/{goal['id']}/plan",
        json={"hours_per_week": 3, "preferences": "No sessions on Sundays"},
        headers=A,
    ).json()
    prompt = str(model.prompts[0][1].content)
    assert "- I study best after 6 pm" in prompt
    assert "- No sessions on Sundays" in prompt
    assert "Weekly budget: 180 minutes" in prompt
    assert body["used_preferences"] == ["I study best after 6 pm", "No sessions on Sundays"]


def test_other_users_memories_do_not_reach_the_prompt(db_app, db_client, goal):
    db_client.post("/chat", json={"message": "I study best at 5 am"}, headers=B)
    model = use_model(db_app, [plan_json(session(today() + timedelta(days=2)))])
    db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 3}, headers=A)
    assert "5 am" not in str(model.prompts[0][1].content)


def test_default_fake_model_end_to_end(db_app, db_client, goal):
    db_app.state.services.planner_model = FakeChatModel()
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 2}, headers=A)
    assert resp.status_code == 201
    tasks = resp.json()["tasks"]
    assert 3 <= len(tasks) <= 5  # one per week over four weeks
    assert all(datetime.fromisoformat(t["due_at"]).hour == 18 for t in tasks)


def test_rate_limit_is_503(db_app, db_client, goal):
    class Limited(FakeChatModel):
        def _generate(self, *a, **k):
            raise ModelRateLimitError("429")

    db_app.state.services.planner_model = Limited()
    resp = db_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 2}, headers=A)
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
