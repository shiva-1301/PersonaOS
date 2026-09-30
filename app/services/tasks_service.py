"""Goals and tasks. Every query is filtered by the current user's id; another user's ids
behave exactly like ids that do not exist (the routers answer 404, never 403)."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db.models import Goal, Task, User
from app.schemas.planner import GoalOut, Progress
from app.services.time_utils import to_utc, today_window, user_zone, week_window


class NotFound(Exception):
    def __init__(self, what: str):
        super().__init__(f"{what} not found")


class InvalidUpdate(Exception):
    pass


# --------------------------------------------------------------------------- goals


def get_goal(db: Session, user_id: uuid.UUID, goal_id: uuid.UUID) -> Goal:
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
    if goal is None:
        raise NotFound("Goal")
    return goal


def progress_for(db: Session, user_id: uuid.UUID, goal_ids: Iterable[uuid.UUID]) -> dict:
    """{goal_id: Progress} with one aggregate query (done / total, 0 when no tasks)."""
    ids = list(goal_ids)
    counts = {gid: (0, 0) for gid in ids}
    if ids:
        rows = db.execute(
            select(
                Task.goal_id,
                func.count(),
                func.count(case((Task.status == "done", 1))),
            )
            .where(Task.user_id == user_id, Task.goal_id.in_(ids))
            .group_by(Task.goal_id)
        )
        for gid, total, done in rows:
            counts[gid] = (done, total)
    return {
        gid: Progress(done=d, total=t, ratio=round(d / t, 4) if t else 0.0)
        for gid, (d, t) in counts.items()
    }


def goal_out(goal: Goal, progress: Progress) -> GoalOut:
    return GoalOut(
        id=goal.id,
        title=goal.title,
        description=goal.description,
        target_date=goal.target_date,
        status=goal.status,
        progress=progress,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def goals_out(db: Session, user_id: uuid.UUID, goals: list[Goal]) -> list[GoalOut]:
    progress = progress_for(db, user_id, [g.id for g in goals])
    return [goal_out(g, progress[g.id]) for g in goals]


# --------------------------------------------------------------------------- tasks


def get_task(db: Session, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise NotFound("Task")
    return task


def list_tasks(
    db: Session,
    user: User,
    *,
    status: str | None = None,
    due: str | None = None,
    goal_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[Task]:
    now = now or datetime.now(UTC)
    tz = user_zone(user.timezone)
    conditions = [Task.user_id == user.id]
    if status:
        conditions.append(Task.status == status)
    if goal_id:
        conditions.append(Task.goal_id == goal_id)
    if due == "today":
        start, end = today_window(now, tz)
        conditions.append(and_(Task.due_at >= start, Task.due_at < end))
    elif due == "this_week":
        start, end = week_window(now, tz)
        conditions.append(and_(Task.due_at >= start, Task.due_at < end))
    elif due == "overdue":
        conditions += [Task.due_at < now, Task.status != "done"]
    return list(
        db.scalars(
            select(Task)
            .where(*conditions)
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at.asc())
        )
    )


def apply_status(task: Task, status: str, now: datetime | None = None) -> None:
    """done sets completed_at (once); leaving done clears it."""
    if status == "done" and task.status != "done":
        task.completed_at = now or datetime.now(UTC)
    elif status != "done":
        task.completed_at = None
    task.status = status


NON_NULLABLE = {"title", "status"}


def update_fields(db: Session, user: User, obj: Goal | Task, changes: dict[str, Any]) -> None:
    """Apply a PATCH (only fields the client sent). Ownership of goal_id is checked."""
    for name in NON_NULLABLE & changes.keys():
        if changes[name] is None:
            raise InvalidUpdate(f"{name} cannot be null")
    tz = user_zone(user.timezone)
    for name, value in changes.items():
        if name == "status" and isinstance(obj, Task):
            apply_status(obj, value)
        elif name == "goal_id" and value is not None:
            get_goal(db, user.id, value)  # raises NotFound for someone else's goal
            obj.goal_id = value
        elif name == "due_at" and value is not None:
            obj.due_at = to_utc(value, tz)
        else:
            setattr(obj, name, value)
