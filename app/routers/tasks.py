import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.db.models import Task
from app.deps import CurrentUser, DbSession
from app.schemas.planner import DueFilter, TaskCreate, TaskOut, TaskStatus, TaskUpdate
from app.services.tasks_service import (
    InvalidUpdate,
    NotFound,
    apply_status,
    get_goal,
    get_task,
    list_tasks,
    update_fields,
)
from app.services.time_utils import to_utc, user_zone

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task(db, user, task_id) -> Task:
    try:
        return get_task(db, user.id, task_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(body: TaskCreate, user: CurrentUser, db: DbSession) -> Task:
    if body.goal_id is not None:
        try:
            get_goal(db, user.id, body.goal_id)
        except NotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    task = Task(
        user_id=user.id,
        goal_id=body.goal_id,
        title=body.title,
        notes=body.notes,
        due_at=to_utc(body.due_at, user_zone(user.timezone)) if body.due_at else None,
        est_minutes=body.est_minutes,
        status="todo",
    )
    apply_status(task, body.status)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=list[TaskOut])
def get_tasks(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    due: Annotated[
        DueFilter | None,
        Query(description="today / this_week (Mon-Sun) in your timezone, or overdue"),
    ] = None,
    goal_id: uuid.UUID | None = None,
) -> list[Task]:
    return list_tasks(db, user, status=status_filter, due=due, goal_id=goal_id)


@router.get("/{task_id}", response_model=TaskOut)
def read_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Task:
    return _task(db, user, task_id)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: uuid.UUID, body: TaskUpdate, user: CurrentUser, db: DbSession) -> Task:
    task = _task(db, user, task_id)
    try:
        update_fields(db, user, task, body.model_dump(exclude_unset=True))
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except InvalidUpdate as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    db.delete(_task(db, user, task_id))
    db.commit()
