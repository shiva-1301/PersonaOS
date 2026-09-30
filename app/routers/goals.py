import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import select

from app.db.models import Goal
from app.deps import CurrentUser, DbSession
from app.schemas.planner import (
    GoalCreate,
    GoalOut,
    GoalStatus,
    GoalUpdate,
    PlanOut,
    PlanRequest,
    TaskOut,
)
from app.services.planner_service import PlanGenerationError, PlanWindowError, generate_study_plan
from app.services.tasks_service import (
    InvalidUpdate,
    NotFound,
    get_goal,
    goal_out,
    goals_out,
    progress_for,
    update_fields,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/goals", tags=["goals"])


def _goal(db, user, goal_id) -> Goal:
    try:
        return get_goal(db, user.id, goal_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(body: GoalCreate, user: CurrentUser, db: DbSession) -> GoalOut:
    goal = Goal(user_id=user.id, **body.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goals_out(db, user.id, [goal])[0]


@router.get("", response_model=list[GoalOut])
def list_goals(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[GoalStatus | None, Query(alias="status")] = None,
) -> list[GoalOut]:
    query = select(Goal).where(Goal.user_id == user.id)
    if status_filter:
        query = query.where(Goal.status == status_filter)
    goals = list(db.scalars(query.order_by(Goal.created_at.desc())))
    return goals_out(db, user.id, goals)


@router.get("/{goal_id}", response_model=GoalOut)
def read_goal(goal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> GoalOut:
    goal = _goal(db, user, goal_id)
    return goal_out(goal, progress_for(db, user.id, [goal.id])[goal.id])


@router.patch("/{goal_id}", response_model=GoalOut)
def patch_goal(goal_id: uuid.UUID, body: GoalUpdate, user: CurrentUser, db: DbSession) -> GoalOut:
    goal = _goal(db, user, goal_id)
    try:
        update_fields(db, user, goal, body.model_dump(exclude_unset=True))
    except InvalidUpdate as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    db.commit()
    db.refresh(goal)
    return goal_out(goal, progress_for(db, user.id, [goal.id])[goal.id])


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """The goal's tasks are kept (their goal link is cleared by ON DELETE SET NULL)."""
    db.delete(_goal(db, user, goal_id))
    db.commit()


@router.post("/{goal_id}/plan", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(
    goal_id: uuid.UUID, body: PlanRequest, request: Request, user: CurrentUser, db: DbSession
) -> PlanOut:
    """Generate dated study sessions for a goal and save them as tasks."""
    goal = _goal(db, user, goal_id)
    try:
        result = generate_study_plan(db, request.app.state.services, user, goal, body)
    except PlanWindowError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except PlanGenerationError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The assistant could not produce a valid study plan after 3 attempts. "
            "Please try again.",
        ) from None
    except ModelRateLimitError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The assistant is busy (rate limited). Please try again in a minute.",
            headers={"Retry-After": "60"},
        ) from None
    except Exception:
        logger.exception("Study plan failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The assistant is temporarily unavailable"
        ) from None
    return PlanOut(
        goal_id=goal.id,
        tasks=[TaskOut.model_validate(t) for t in result.tasks],
        total_minutes=sum(t.est_minutes or 0 for t in result.tasks),
        adjustments=result.adjustments,
        used_preferences=result.used_preferences,
    )
