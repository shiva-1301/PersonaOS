"""Goals and tasks: CRUD, isolation (404 not 403), progress, completed_at, due filters."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.models import Task, User
from app.services.tasks_service import list_tasks
from tests.conftest import auth

A, B = auth("test-user-a"), auth("test-user-b")


def make_goal(client, headers=A, **kw):
    resp = client.post("/goals", json={"title": "Finish ML course", **kw}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_task(client, headers=A, **kw):
    resp = client.post("/tasks", json={"title": "Read chapter", **kw}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- goals


def test_goal_crud(db_client):
    goal = make_goal(db_client, description="Andrew Ng", target_date="2026-11-30")
    assert goal["status"] == "active"
    assert goal["progress"] == {"done": 0, "total": 0, "ratio": 0.0}

    got = db_client.get(f"/goals/{goal['id']}", headers=A).json()
    assert got["target_date"] == "2026-11-30"

    patched = db_client.patch(
        f"/goals/{goal['id']}", json={"status": "paused", "title": "ML course"}, headers=A
    ).json()
    assert (patched["status"], patched["title"], patched["description"]) == (
        "paused",
        "ML course",
        "Andrew Ng",  # untouched field kept
    )
    assert [g["id"] for g in db_client.get("/goals?status=paused", headers=A).json()] == [
        goal["id"]
    ]
    assert db_client.get("/goals?status=active", headers=A).json() == []

    assert db_client.delete(f"/goals/{goal['id']}", headers=A).status_code == 204
    assert db_client.get(f"/goals/{goal['id']}", headers=A).status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"status": "done"}],
    ids=["empty", "blank", "too-long", "bad-status"],
)
def test_goal_validation(db_client, body):
    goal = make_goal(db_client)
    assert db_client.post("/goals", json={"title": "ok", **body}, headers=A).status_code == 422
    assert db_client.patch(f"/goals/{goal['id']}", json=body, headers=A).status_code == 422


def test_goal_title_cannot_be_nulled(db_client):
    goal = make_goal(db_client)
    resp = db_client.patch(f"/goals/{goal['id']}", json={"title": None}, headers=A)
    assert resp.status_code == 422


def test_deleting_goal_keeps_its_tasks(db_client):
    goal = make_goal(db_client)
    task = make_task(db_client, goal_id=goal["id"])
    db_client.delete(f"/goals/{goal['id']}", headers=A)
    assert db_client.get(f"/tasks/{task['id']}", headers=A).json()["goal_id"] is None


def test_progress_math(db_client):
    goal = make_goal(db_client)
    ids = [make_task(db_client, goal_id=goal["id"])["id"] for _ in range(3)]
    make_task(db_client)  # not linked: does not count
    db_client.patch(f"/tasks/{ids[0]}", json={"status": "done"}, headers=A)
    assert db_client.get(f"/goals/{goal['id']}", headers=A).json()["progress"] == {
        "done": 1,
        "total": 3,
        "ratio": 0.3333,
    }
    for tid in ids[1:]:
        db_client.patch(f"/tasks/{tid}", json={"status": "done"}, headers=A)
    listed = db_client.get("/goals", headers=A).json()
    assert listed[0]["progress"] == {"done": 3, "total": 3, "ratio": 1.0}


# --------------------------------------------------------------------------- tasks


def test_task_crud_and_completed_at(db_client):
    task = make_task(db_client, notes="p. 10-20", est_minutes=45)
    assert (task["status"], task["completed_at"]) == ("todo", None)

    done = db_client.patch(f"/tasks/{task['id']}", json={"status": "done"}, headers=A).json()
    assert done["completed_at"] is not None
    again = db_client.patch(f"/tasks/{task['id']}", json={"status": "done"}, headers=A).json()
    assert again["completed_at"] == done["completed_at"]  # re-marking done keeps the time
    back = db_client.patch(f"/tasks/{task['id']}", json={"status": "doing"}, headers=A).json()
    assert back["completed_at"] is None

    edited = db_client.patch(
        f"/tasks/{task['id']}", json={"notes": None, "est_minutes": 30}, headers=A
    ).json()
    assert (edited["notes"], edited["est_minutes"], edited["title"]) == (None, 30, "Read chapter")
    assert db_client.delete(f"/tasks/{task['id']}", headers=A).status_code == 204
    assert db_client.get(f"/tasks/{task['id']}", headers=A).status_code == 404


def test_create_task_already_done_sets_completed_at(db_client):
    assert make_task(db_client, status="done")["completed_at"] is not None


@pytest.mark.parametrize(
    "body",
    [{"title": ""}, {"est_minutes": 0}, {"est_minutes": 2000}, {"status": "finished"}],
    ids=["empty-title", "zero-minutes", "too-many-minutes", "bad-status"],
)
def test_task_validation(db_client, body):
    assert db_client.post("/tasks", json={"title": "ok", **body}, headers=A).status_code == 422


def test_naive_due_at_is_the_users_local_time(db_client):
    assert db_client.patch("/me", json={"timezone": "Asia/Kolkata"}, headers=A).status_code == 200
    task = make_task(db_client, due_at="2026-11-01T18:00:00")
    assert task["due_at"].startswith("2026-11-01T12:30:00")  # 18:00 IST = 12:30 UTC
    aware = make_task(db_client, due_at="2026-11-01T18:00:00+00:00")
    assert aware["due_at"].startswith("2026-11-01T18:00:00")


def test_invalid_timezone_rejected(db_client):
    resp = db_client.patch("/me", json={"timezone": "Mars/Olympus"}, headers=A)
    assert resp.status_code == 422
    assert db_client.patch("/me", json={"timezone": None}, headers=A).status_code == 422
    assert db_client.get("/me", headers=A).json()["timezone"] == "UTC"


# --------------------------------------------------------------------------- isolation


def test_other_users_goals_and_tasks_are_404(db_client):
    goal = make_goal(db_client)
    task = make_task(db_client, goal_id=goal["id"])

    for method, path, body in [
        ("get", f"/goals/{goal['id']}", None),
        ("patch", f"/goals/{goal['id']}", {"title": "hijack"}),
        ("delete", f"/goals/{goal['id']}", None),
        ("post", f"/goals/{goal['id']}/plan", {"hours_per_week": 5, "end_date": "2026-12-01"}),
        ("get", f"/tasks/{task['id']}", None),
        ("patch", f"/tasks/{task['id']}", {"status": "done"}),
        ("delete", f"/tasks/{task['id']}", None),
    ]:
        kwargs = {"json": body} if body else {}
        resp = getattr(db_client, method)(path, headers=B, **kwargs)
        assert resp.status_code == 404, (method, path, resp.status_code)

    assert db_client.get("/goals", headers=B).json() == []
    assert db_client.get("/tasks", headers=B).json() == []
    assert db_client.get(f"/tasks?goal_id={goal['id']}", headers=B).json() == []
    # A's data is unchanged.
    assert db_client.get(f"/tasks/{task['id']}", headers=A).json()["status"] == "todo"
    assert db_client.get(f"/goals/{goal['id']}", headers=A).json()["title"] == "Finish ML course"


def test_cannot_link_task_to_another_users_goal(db_client):
    a_goal = make_goal(db_client)
    resp = db_client.post("/tasks", json={"title": "x", "goal_id": a_goal["id"]}, headers=B)
    assert resp.status_code == 404
    b_task = make_task(db_client, headers=B)
    resp = db_client.patch(f"/tasks/{b_task['id']}", json={"goal_id": a_goal["id"]}, headers=B)
    assert resp.status_code == 404
    assert db_client.get(f"/goals/{a_goal['id']}", headers=A).json()["progress"]["total"] == 0


# --------------------------------------------------------------------------- due filters


@pytest.fixture
def user_in_kolkata(db_client, db_session) -> User:
    db_client.patch("/me", json={"timezone": "Asia/Kolkata"}, headers=A)
    return db_session.scalar(select(User).where(User.auth_uid == "test-user-a"))


def _add(db, user, title, due, status="todo"):
    db.add(Task(id=uuid.uuid4(), user_id=user.id, title=title, due_at=due, status=status))
    db.commit()


def test_due_filters_use_the_users_timezone(db_session, user_in_kolkata):
    user = user_in_kolkata
    ist = ZoneInfo("Asia/Kolkata")
    # 20:00 UTC Monday 5 Oct = 01:30 IST Tuesday 6 Oct: "today" is the 6th in Kolkata.
    now = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)

    def local(day, hour):
        return datetime(2026, 10, day, hour, 0, tzinfo=ist)

    _add(db_session, user, "today-evening", local(6, 18))
    _add(db_session, user, "yesterday-open", local(5, 18))  # overdue, same week
    _add(db_session, user, "yesterday-done", local(5, 9), status="done")
    _add(db_session, user, "sunday", local(11, 20))  # this week (Mon 5 - Sun 11)
    _add(db_session, user, "next-monday", local(12, 9))
    _add(db_session, user, "no-date", None)

    def titles(**kw):
        return [t.title for t in list_tasks(db_session, user, now=now, **kw)]

    assert titles(due="today") == ["today-evening"]
    assert titles(due="overdue") == ["yesterday-open"]
    assert titles(due="this_week") == [
        "yesterday-done",
        "yesterday-open",
        "today-evening",
        "sunday",
    ]
    assert titles(status="done") == ["yesterday-done"]
    assert titles()[-1] == "no-date"  # undated tasks sort last


def test_due_filter_via_api(db_client):
    now = datetime.now(UTC)
    make_task(db_client, title="overdue", due_at=(now - timedelta(days=2)).isoformat())
    make_task(db_client, title="later", due_at=(now + timedelta(days=30)).isoformat())
    resp = db_client.get("/tasks?due=overdue", headers=A).json()
    assert [t["title"] for t in resp] == ["overdue"]
    assert db_client.get("/tasks?due=someday", headers=A).status_code == 422
