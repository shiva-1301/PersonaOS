"""Clear study-plan requests are planned in code, even when the model would rather ask
for dates (what qwen2.5:7b did in about 1 of 6 Docker runs)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.agent.study_plan_guard import requested_hours
from app.db.models import Task
from app.services.fakes import FakeChatModel
from tests.conftest import auth
from tests.test_agent import call, chat, last_tool_result, script

A, B = auth("test-user-a"), auth("test-user-b")
ASKS_FOR_DATES = "Great! When would you like to start, and when do you want to finish?"


@pytest.mark.parametrize(
    ("message", "hours"),
    [
        ("Make me a study plan for my ML course goal, 6 hours a week", 6),
        ("Can you create a study plan for Finish ML course with six hours per week?", 6),
        ("please build a study schedule for my goal, 4.5 hrs/week", 4.5),
        ("I need a study plan for the ML course: 10 hours weekly", 10),
    ],
)
def test_clear_requests_are_recognised(message, hours):
    assert requested_hours(message) == hours


@pytest.mark.parametrize(
    "message",
    [
        "What does my study plan look like?",  # a question, no hours
        "Make me a study plan for my ML course",  # no hours: the model asks
        "Make me a study plan, 6 hours a week, starting next Monday",  # dates: model
        "Make me a study plan on weekends, 6 hours a week",  # preferences: model
        "Make me a study plan, 6 hours a week, evenings only",
        "Don't make a study plan for 6 hours a week",
        "Add a task to study 6 hours a week",  # not a study plan
        "Make me a study plan, 200 hours a week",  # out of range
    ],
)
def test_anything_less_clear_is_left_to_the_model(message):
    assert requested_hours(message) is None


def _goal(client, headers, title="Finish ML course", days=27):
    target = (datetime.now(UTC) + timedelta(days=days)).date().isoformat() if days else None
    return client.post(
        "/goals", json={"title": title, "target_date": target}, headers=headers
    ).json()


def _tasks(db_session, goal) -> list[Task]:
    return db_session.scalars(select(Task).where(Task.goal_id == uuid.UUID(goal["id"]))).all()


@pytest.fixture
def planner(db_app):
    db_app.state.services.planner_model = FakeChatModel()  # deterministic plan JSON


def test_plan_is_created_in_code_when_the_model_asks_for_dates(
    planner, db_app, db_client, db_session
):
    goal = _goal(db_client, A)
    model = script(db_app, ASKS_FOR_DATES)
    body = chat(db_client, A, "Make me a study plan for my ML course goal, 6 hours a week")

    tasks = _tasks(db_session, goal)
    assert len(tasks) >= 3
    assert body["tools_used"] == ["generate_study_plan"]
    system = model.prompts[0][0].content
    assert "ALREADY created" in system and f"{len(tasks)} sessions" in system
    assert "do not ask for dates" in system


def test_model_cannot_make_a_second_plan_in_the_same_message(
    planner, db_app, db_client, db_session
):
    goal = _goal(db_client, A)
    model = script(
        db_app,
        call("generate_study_plan", goal_id="Finish ML course", hours_per_week=6),
        "Here is your plan.",
    )
    body = chat(db_client, A, "Make me a study plan for my ML course goal, 6 hours a week")
    first_plan = len(_tasks(db_session, goal))
    assert first_plan >= 3  # created once, by the guard
    # The tool isn't even offered to the model after the guard made the plan ...
    assert "generate_study_plan" not in model.tools_per_call[0]
    # ... and a model that calls it anyway is refused, and that refusal isn't reported
    # as a second use of the tool.
    assert "already created" in last_tool_result(model.prompts[1])["error"]
    assert body["tools_used"] == ["generate_study_plan"]


def test_ambiguous_goal_is_left_to_the_model(planner, db_app, db_client, db_session):
    g1 = _goal(db_client, A, "Finish ML course")
    g2 = _goal(db_client, A, "Learn Spanish")
    script(db_app, "Which goal is this for?")
    body = chat(db_client, A, "Make me a study plan for my goal, 6 hours a week")
    assert body["tools_used"] == []
    assert _tasks(db_session, g1) == [] and _tasks(db_session, g2) == []


def test_title_match_picks_the_named_goal(planner, db_app, db_client, db_session):
    ml = _goal(db_client, A, "Finish ML course")
    spanish = _goal(db_client, A, "Learn Spanish")
    script(db_app, ASKS_FOR_DATES)
    chat(db_client, A, "Make me a study plan for Spanish, 5 hours a week")
    assert len(_tasks(db_session, spanish)) >= 3 and _tasks(db_session, ml) == []


def test_goal_without_target_date_is_left_to_the_model(planner, db_app, db_client, db_session):
    goal = _goal(db_client, A, days=None)
    script(db_app, "Until when should the plan run?")
    body = chat(db_client, A, "Make me a study plan for my ML course goal, 6 hours a week")
    assert body["tools_used"] == [] and _tasks(db_session, goal) == []


def test_guard_only_touches_the_callers_own_goal(planner, db_app, db_client, db_session):
    a_goal = _goal(db_client, A)
    b_goal = _goal(db_client, B)  # same title
    script(db_app, ASKS_FOR_DATES)
    chat(db_client, B, "Make me a study plan for my ML course goal, 6 hours a week")
    assert len(_tasks(db_session, b_goal)) >= 3
    assert _tasks(db_session, a_goal) == []


def test_streamed_request_reports_the_plan_tool(planner, db_app, db_client, db_session):
    goal = _goal(db_client, A)
    script(db_app, "Here is your plan.")
    with db_client.stream(
        "POST",
        "/chat/stream",
        json={"message": "Make me a study plan for my ML course goal, 6 hours a week"},
        headers=A,
    ) as resp:
        body = "".join(resp.iter_text())
    assert resp.status_code == 200
    assert "event: tool" in body and "generate_study_plan" in body
    assert len(_tasks(db_session, goal)) >= 3
