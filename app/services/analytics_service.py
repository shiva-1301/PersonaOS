"""Dashboard numbers for one user, computed with SQL aggregates.

Every query filters on the caller's user_id. Days and weeks are the user's local
calendar days (users.timezone): Postgres converts completed_at with timezone(),
so a task finished at 23:30 in Kolkata counts for that Kolkata day.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import MEMORY_STATES, TASK_STATUSES, Goal, MemoryMeta, Task, User
from app.services.tasks_service import progress_for
from app.services.time_utils import local_midnight, user_zone

DUE_SOON_HOURS = 48
STREAK_LOOKBACK_DAYS = 365


def _streaks(days_with_completions: set[date], today: date) -> tuple[int, int]:
    """(current, longest). The current streak may end yesterday: today isn't over."""
    day = today if today in days_with_completions else today - timedelta(days=1)
    current = 0
    while day in days_with_completions:
        current += 1
        day -= timedelta(days=1)
    longest = run = 0
    previous = None
    for d in sorted(days_with_completions):
        run = run + 1 if previous is not None and d - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = d
    return current, longest


def summary(
    db: Session, user: User, *, days: int = 30, weeks: int = 12, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    tz = user_zone(user.timezone)
    today = now.astimezone(tz).date()
    mine = Task.user_id == user.id
    done = Task.status == "done"

    # completed_at as local wall-clock time, then its local date / week (Monday).
    local_completed = func.timezone(tz.key, Task.completed_at)
    local_day = cast(local_completed, Date)
    local_week = cast(func.date_trunc("week", local_completed), Date)

    first_day = today - timedelta(days=days - 1)
    per_day = dict.fromkeys((first_day + timedelta(days=i) for i in range(days)), 0)
    for day, count in db.execute(
        select(local_day, func.count())
        .where(mine, done, Task.completed_at >= local_midnight(first_day, tz).astimezone(UTC))
        .group_by(local_day)
    ):
        if day in per_day:
            per_day[day] = count

    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    per_week = dict.fromkeys((first_monday + timedelta(weeks=i) for i in range(weeks)), 0)
    for week, count in db.execute(
        select(local_week, func.count())
        .where(mine, done, Task.completed_at >= local_midnight(first_monday, tz).astimezone(UTC))
        .group_by(local_week)
    ):
        if week in per_week:
            per_week[week] = count

    lookback = local_midnight(today - timedelta(days=STREAK_LOOKBACK_DAYS), tz).astimezone(UTC)
    active_days = set(
        db.scalars(select(local_day).where(mine, done, Task.completed_at >= lookback).distinct())
    )
    current, longest = _streaks(active_days, today)

    by_status = dict.fromkeys(TASK_STATUSES, 0)
    for status, count in db.execute(
        select(Task.status, func.count()).where(mine).group_by(Task.status)
    ):
        by_status[status] = count
    open_task = Task.status != "done"
    overdue = db.scalar(select(func.count()).where(mine, open_task, Task.due_at < now))
    due_soon = db.scalar(
        select(func.count()).where(
            mine,
            open_task,
            Task.due_at >= now,
            Task.due_at < now + timedelta(hours=DUE_SOON_HOURS),
        )
    )

    goals = list(db.scalars(select(Goal).where(Goal.user_id == user.id).order_by(Goal.created_at)))
    progress = progress_for(db, user.id, [g.id for g in goals])

    memory = dict.fromkeys(MEMORY_STATES, 0)
    for state, count in db.execute(
        select(MemoryMeta.state, func.count())
        .where(MemoryMeta.user_id == user.id)
        .group_by(MemoryMeta.state)
    ):
        memory[state] = count

    last_7 = today - timedelta(days=6)
    return {
        "timezone": tz.key,
        "today": today,
        "due_soon_hours": DUE_SOON_HOURS,
        "completions_per_day": [{"date": d, "completed": n} for d, n in per_day.items()],
        "completions_per_week": [{"week_start": w, "completed": n} for w, n in per_week.items()],
        "completed_last_7_days": sum(n for d, n in per_day.items() if d >= last_7),
        "streak": {"current": current, "longest": longest, "completed_today": today in active_days},
        "tasks": {**by_status, "overdue": overdue, "due_soon": due_soon},
        "goals": [
            {
                "id": g.id,
                "title": g.title,
                "status": g.status,
                "target_date": g.target_date,
                "done": progress[g.id].done,
                "total": progress[g.id].total,
                "ratio": progress[g.id].ratio,
            }
            for g in goals
        ],
        "memory_by_state": memory,
    }
