"""GET /analytics/summary: SQL aggregates in the user's timezone, scoped to the user."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db.models import Goal, MemoryMeta, Task, User
from app.services.analytics_service import _streaks, summary
from tests.conftest import auth

A, B = auth("test-user-a"), auth("test-user-b")
# Thursday 1 Oct 2026, 17:30 in Kolkata.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def user(db_client, db_session, headers, timezone="Asia/Kolkata") -> User:
    assert db_client.patch("/me", json={"timezone": timezone}, headers=headers).status_code == 200
    uid = db_client.get("/me", headers=headers).json()["id"]
    return db_session.get(User, uuid.UUID(uid))


def done_at(db_session, owner: User, *moments: datetime, goal: Goal | None = None) -> None:
    for i, moment in enumerate(moments):
        db_session.add(
            Task(
                user_id=owner.id,
                goal_id=goal.id if goal else None,
                title=f"done {i}",
                status="done",
                completed_at=moment,
            )
        )
    db_session.commit()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_requires_login(db_client):
    assert db_client.get("/analytics/summary").status_code == 401


def test_new_user_gets_zeros(db_client):
    body = db_client.get("/analytics/summary", headers=A).json()
    assert len(body["completions_per_day"]) == 30 and len(body["completions_per_week"]) == 12
    assert all(d["completed"] == 0 for d in body["completions_per_day"])
    assert body["streak"] == {"current": 0, "longest": 0, "completed_today": False}
    assert body["tasks"] == {"todo": 0, "doing": 0, "done": 0, "overdue": 0, "due_soon": 0}
    assert body["goals"] == []
    assert body["memory_by_state"] == {"active": 0, "stale": 0, "archived": 0, "superseded": 0}


def test_completions_are_counted_on_the_users_local_day_and_week(db_client, db_session):
    a = user(db_client, db_session, A)
    done_at(
        db_session,
        a,
        utc(2026, 9, 30, 19, 0),  # 1 Oct 00:30 in Kolkata (still 30 Sep in UTC)
        utc(2026, 9, 30, 18, 0),  # 30 Sep 23:30 in Kolkata
        utc(2026, 9, 28, 10, 0),  # Monday 28 Sep
        utc(2026, 9, 27, 10, 0),  # Sunday 27 Sep: the previous week
        utc(2026, 8, 1, 10, 0),  # outside the 30 days, inside the 12 weeks
    )
    db_session.add(Task(user_id=a.id, title="not done", status="doing"))
    db_session.commit()

    s = summary(db_session, a, now=NOW)
    per_day = {d["date"]: d["completed"] for d in s["completions_per_day"]}
    assert s["today"] == date(2026, 10, 1) and s["timezone"] == "Asia/Kolkata"
    assert min(per_day) == date(2026, 9, 2) and max(per_day) == date(2026, 10, 1)
    assert per_day[date(2026, 10, 1)] == 1
    assert per_day[date(2026, 9, 30)] == 1
    assert per_day[date(2026, 9, 28)] == 1 and per_day[date(2026, 9, 27)] == 1
    assert sum(per_day.values()) == 4

    per_week = {w["week_start"]: w["completed"] for w in s["completions_per_week"]}
    assert max(per_week) == date(2026, 9, 28) and len(per_week) == 12
    assert per_week[date(2026, 9, 28)] == 3
    assert per_week[date(2026, 9, 21)] == 1
    assert per_week[date(2026, 7, 27)] == 1

    assert s["completed_last_7_days"] == 4
    assert s["streak"] == {"current": 2, "longest": 2, "completed_today": True}
    assert s["tasks"]["done"] == 5 and s["tasks"]["doing"] == 1


def test_the_same_moments_in_utc_fall_on_other_days(db_client, db_session):
    a = user(db_client, db_session, A, timezone="UTC")
    done_at(db_session, a, utc(2026, 9, 30, 19, 0), utc(2026, 9, 30, 18, 0))
    per_day = {
        d["date"]: d["completed"] for d in summary(db_session, a, now=NOW)["completions_per_day"]
    }
    assert per_day[date(2026, 9, 30)] == 2 and per_day[date(2026, 10, 1)] == 0


def test_streak_can_end_yesterday_and_breaks_on_a_gap():
    today = date(2026, 10, 1)
    days = {today - timedelta(days=n) for n in (1, 2, 3, 6, 7, 8, 9)}
    assert _streaks(days, today) == (3, 4)  # today isn't over: the run ending yesterday counts
    assert _streaks(days | {today}, today) == (4, 4)
    assert _streaks({today - timedelta(days=2)}, today) == (0, 1)
    assert _streaks(set(), today) == (0, 0)


def test_overdue_and_due_soon(db_client, db_session):
    a = user(db_client, db_session, A)
    now = datetime.now(UTC)
    rows = [
        ("overdue", "todo", now - timedelta(hours=3)),
        ("overdue doing", "doing", now - timedelta(days=2)),
        ("late but done", "done", now - timedelta(days=1)),
        ("soon", "todo", now + timedelta(hours=10)),
        ("later", "todo", now + timedelta(days=3)),
        ("no date", "todo", None),
    ]
    for title, status, due in rows:
        db_session.add(
            Task(
                user_id=a.id,
                title=title,
                status=status,
                due_at=due,
                completed_at=now if status == "done" else None,
            )
        )
    db_session.commit()
    tasks = db_client.get("/analytics/summary", headers=A).json()["tasks"]
    assert tasks == {"todo": 4, "doing": 1, "done": 1, "overdue": 2, "due_soon": 1}


def test_goal_progress_and_memory_states(db_client, db_session):
    a = user(db_client, db_session, A)
    goal = Goal(user_id=a.id, title="Finish ML course", target_date=date(2026, 11, 30))
    empty = Goal(user_id=a.id, title="Learn Spanish")
    db_session.add_all([goal, empty])
    db_session.commit()
    done_at(db_session, a, datetime.now(UTC), goal=goal)
    db_session.add_all(
        [
            Task(user_id=a.id, goal_id=goal.id, title="t1", status="todo"),
            Task(user_id=a.id, goal_id=goal.id, title="t2", status="doing"),
            *[
                MemoryMeta(mem0_id=f"m{i}", user_id=a.id, state=state)
                for i, state in enumerate(["active", "active", "stale", "superseded"])
            ],
        ]
    )
    db_session.commit()

    body = db_client.get("/analytics/summary", headers=A).json()
    goals = {g["title"]: g for g in body["goals"]}
    assert goals["Finish ML course"]["done"] == 1 and goals["Finish ML course"]["total"] == 3
    assert goals["Finish ML course"]["ratio"] == 0.3333  # rounded like GET /goals
    assert goals["Finish ML course"]["target_date"] == "2026-11-30"
    assert (goals["Learn Spanish"]["total"], goals["Learn Spanish"]["ratio"]) == (0, 0.0)
    assert body["memory_by_state"] == {"active": 2, "stale": 1, "archived": 0, "superseded": 1}


def test_only_the_callers_data_is_counted(db_client, db_session):
    user(db_client, db_session, A)
    b = user(db_client, db_session, B)
    goal = Goal(user_id=b.id, title="B's goal")
    db_session.add(goal)
    db_session.commit()
    done_at(db_session, b, datetime.now(UTC), datetime.now(UTC) - timedelta(days=1), goal=goal)
    db_session.add_all(
        [
            Task(
                user_id=b.id,
                title="B overdue",
                status="todo",
                due_at=datetime.now(UTC) - timedelta(hours=1),
            ),
            MemoryMeta(mem0_id="b-mem", user_id=b.id, state="active"),
        ]
    )
    db_session.commit()

    a_body = db_client.get("/analytics/summary", headers=A).json()
    assert sum(d["completed"] for d in a_body["completions_per_day"]) == 0
    assert a_body["streak"]["current"] == 0 and a_body["goals"] == []
    assert a_body["tasks"]["overdue"] == 0 and a_body["memory_by_state"]["active"] == 0

    b_body = db_client.get("/analytics/summary", headers=B).json()
    assert b_body["streak"]["current"] == 2 and b_body["tasks"]["overdue"] == 1
    assert [g["title"] for g in b_body["goals"]] == ["B's goal"]
    assert db_session.scalars(select(Task).where(Task.user_id == b.id)).all()


def test_window_sizes_are_bounded(db_client):
    assert db_client.get("/analytics/summary?days=6", headers=A).status_code == 422
    assert db_client.get("/analytics/summary?weeks=60", headers=A).status_code == 422
    body = db_client.get("/analytics/summary?days=7&weeks=4", headers=A).json()
    assert len(body["completions_per_day"]) == 7 and len(body["completions_per_week"]) == 4
