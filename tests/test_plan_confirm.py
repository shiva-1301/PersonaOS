"""A "yes" to a study plan the assistant proposed is carried out in code.

Replays the UI conversation that failed: the assistant proposed a schedule for an
English exam that wasn't a goal yet, the user said "yes", and the model answered in
text without making the plan.
"""

import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.agent.plan_confirm import CONFIRM_MARKER
from app.db.models import Goal, Task
from app.services.fakes import ScriptedChatModel, fake_plan
from tests.conftest import auth
from tests.test_agent import chat, script

A, B = auth("test-user-a"), auth("test-user-b")
PROPOSAL = (
    "Here's a proposed schedule for your English exam, 5 hours a week:\n"
    "- Mon 14:00-16:00\n- Wed 15:00-17:00\n- Fri 13:00-15:00\nShall I create this study plan?"
)


def exam_day() -> date:
    return (datetime.now(UTC) + timedelta(days=11)).date()


def agreed(**overrides) -> dict:
    return {
        "confirmed": True,
        "goal": "English exam",
        "end_date": exam_day().isoformat(),
        "hours_per_week": 5,
        "preferences": "Mon 14:00-16:00, Wed 15:00-17:00, Fri 13:00-15:00",
        **overrides,
    }


def json_model(db_app, extraction) -> ScriptedChatModel:
    """The JSON-mode model: answers the confirmation question, and plans like the fake."""

    def reply(messages):
        if CONFIRM_MARKER in str(messages[0].content):
            return extraction if isinstance(extraction, str) else json.dumps(extraction)
        return fake_plan(str(next(m for m in messages if m.type == "human").content))

    model = ScriptedChatModel(replies=[reply])
    model.prompts, model.tools_per_call = [], []
    db_app.state.services.planner_model = model
    return model


def confirmation_prompts(model) -> list:
    return [p for p in model.prompts if CONFIRM_MARKER in str(p[0].content)]


def proposal_then(db_client, db_app, answer: str, *, extraction, reply="Here is your plan."):
    script(db_app, PROPOSAL)
    first = chat(db_client, A, "plan my english exam on 12 oct, 5 hours a week, suggest times")
    planner = json_model(db_app, extraction)
    model = script(db_app, reply)  # the agent answers in text, no tool call
    body = db_client.post(
        "/chat", json={"message": answer, "session_id": first["session_id"]}, headers=A
    ).json()
    return body, model, planner


def test_yes_to_a_proposed_plan_creates_the_goal_and_the_plan(db_app, db_client, db_session):
    body, model, planner = proposal_then(db_client, db_app, "yes", extraction=agreed())

    goal = db_session.scalar(select(Goal))
    assert (goal.title, goal.target_date) == ("English exam", exam_day())
    tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
    assert tasks, "the plan's sessions were saved"
    assert body["tool_results"] == [{"tool": "generate_study_plan", "ok": True}]
    (asked,) = confirmation_prompts(planner)
    assert "Shall I create this study plan?" in asked[-1].content

    # A bare yes gets a reply built from the saved sessions, not the model's retelling.
    assert model.prompts == []
    reply = body["reply"]
    assert reply.startswith('Done: I added "English exam" as a goal and made its study plan')
    first = min(tasks, key=lambda t: t.due_at)
    assert f"{first.due_at.astimezone(UTC):%a %Y-%m-%d}" in reply or first.title in reply


def test_a_longer_yes_still_lets_the_agent_answer_the_rest(db_app, db_client, db_session):
    body, model, _ = proposal_then(
        db_client, db_app, "yes please, and tell me how to revise grammar", extraction=agreed()
    )
    assert db_session.scalars(select(Task)).all()
    assert body["tool_results"] == [{"tool": "generate_study_plan", "ok": True}]
    # The agent is told what was created and isn't offered the plan tool again.
    system = model.prompts[0][0].content
    assert "ALREADY created" in system and "created just now" in system
    assert "generate_study_plan" not in model.tools_per_call[0]


def test_a_yes_to_something_else_changes_nothing(db_app, db_client, db_session):
    body, _, planner = proposal_then(db_client, db_app, "yes", extraction=agreed(confirmed=False))
    assert confirmation_prompts(planner)
    assert db_session.scalars(select(Goal)).all() == []
    assert body["tool_results"] == []


def test_no_plan_talk_means_no_extra_model_call(db_app, db_client, db_session):
    script(db_app, "Shall I remind you about it?")
    first = chat(db_client, A, "my english exam is on 12 oct")
    planner = json_model(db_app, agreed())
    script(db_app, "OK.")
    db_client.post("/chat", json={"message": "yes", "session_id": first["session_id"]}, headers=A)
    assert confirmation_prompts(planner) == []
    assert db_session.scalars(select(Goal)).all() == []


def test_only_a_clear_yes_triggers_it(db_app, db_client, db_session):
    _, _, planner = proposal_then(
        db_client, db_app, "no, make it mornings instead", extraction=agreed()
    )
    assert confirmation_prompts(planner) == []
    assert db_session.scalars(select(Goal)).all() == []


def test_a_new_goal_without_a_date_is_not_guessed(db_app, db_client, db_session):
    body, model, _ = proposal_then(
        db_client, db_app, "yes", extraction=agreed(end_date=None), reply="When is the exam?"
    )
    assert db_session.scalars(select(Goal)).all() == []
    assert body["tool_results"] == [{"tool": "generate_study_plan", "ok": False}]
    system = model.prompts[0][0].content
    assert "failed" in system and "end_date" in system


def test_unreadable_answers_change_nothing(db_app, db_client, db_session):
    for extraction in ("not json at all", agreed(hours_per_week=500), agreed(hours_per_week=None)):
        body, _, _ = proposal_then(db_client, db_app, "yes", extraction=extraction)
        assert body["tool_results"] == []
    assert db_session.scalars(select(Goal)).all() == []


def test_an_existing_goal_with_a_plan_is_not_planned_twice(db_app, db_client, db_session):
    goal = db_client.post(
        "/goals", json={"title": "English exam", "target_date": exam_day().isoformat()}, headers=A
    ).json()
    db_client.post("/tasks", json={"title": "Read", "goal_id": goal["id"]}, headers=A)
    body, _, _ = proposal_then(db_client, db_app, "yes", extraction=agreed())
    assert body["tool_results"] == []
    assert len(db_session.scalars(select(Task)).all()) == 1


def test_it_only_ever_touches_the_callers_goals(db_app, db_client, db_session):
    b_goal = db_client.post(
        "/goals", json={"title": "English exam", "target_date": exam_day().isoformat()}, headers=B
    ).json()
    proposal_then(db_client, db_app, "yes", extraction=agreed())
    a_goals = db_client.get("/goals", headers=A).json()
    assert [g["title"] for g in a_goals] == ["English exam"]  # A got its own new goal
    assert a_goals[0]["id"] != b_goal["id"] and a_goals[0]["progress"]["total"] > 0
    assert db_client.get("/goals", headers=B).json()[0]["progress"]["total"] == 0


def test_saved_replies_get_weekdays_that_match_their_dates(db_app, db_client):
    script(db_app, "Your first session is on Monday, 2026-10-02 at 14:00.")
    body = chat(db_client, A, "when is my first session?")
    assert body["reply"] == "Your first session is on Friday, 2026-10-02 at 14:00."
    messages = db_client.get(f"/chat/sessions/{body['session_id']}", headers=A).json()["messages"]
    assert messages[-1]["content"] == body["reply"]
