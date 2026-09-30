"""Goals, tasks and study-plan request/response models.

Datetimes in requests may be naive (read as the user's local time) or carry an offset;
responses are always UTC.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GoalStatus = Literal["active", "completed", "paused"]
TaskStatus = Literal["todo", "doing", "done"]
DueFilter = Literal["today", "this_week", "overdue"]


def _strip(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("must not be blank")
    return v


# --------------------------------------------------------------------------- goals


class GoalCreate(BaseModel):
    title: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    target_date: date | None = None
    status: GoalStatus = "active"

    _title = field_validator("title")(_strip)


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    target_date: date | None = None
    status: GoalStatus | None = None

    _title = field_validator("title")(_strip)


class Progress(BaseModel):
    done: int
    total: int
    ratio: float  # done / total, 0.0 when there are no tasks


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    target_date: date | None
    status: GoalStatus
    progress: Progress
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- tasks


class TaskCreate(BaseModel):
    title: str = Field(max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: datetime | None = None
    goal_id: uuid.UUID | None = None
    est_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    status: TaskStatus = "todo"

    _title = field_validator("title")(_strip)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: datetime | None = None
    goal_id: uuid.UUID | None = None
    est_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    status: TaskStatus | None = None

    _title = field_validator("title")(_strip)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal_id: uuid.UUID | None
    title: str
    notes: str | None
    due_at: datetime | None
    est_minutes: int | None
    status: TaskStatus
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- study plans


class PlanRequest(BaseModel):
    hours_per_week: float = Field(gt=0, le=80)
    # Defaults: start = today, end = the goal's target date.
    start_date: date | None = None
    end_date: date | None = None
    # Extra preferences for this plan; remembered preferences are added automatically.
    preferences: str | None = Field(default=None, max_length=1000)
    # Spread sessions evenly across the whole window (at the plan's usual time of day)
    # instead of keeping the model's own dates, which tend to bunch up at the start.
    spread_evenly: bool = True

    @model_validator(mode="after")
    def _order(self) -> "PlanRequest":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class PlanOut(BaseModel):
    goal_id: uuid.UUID
    tasks: list[TaskOut]
    total_minutes: int
    # Deterministic adjustments applied to the model's plan (moved/dropped/clamped).
    adjustments: list[str]
    used_preferences: list[str]


class LLMPlanTask(BaseModel):
    """One session as the LLM must return it (validated before anything is saved)."""

    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime
    est_minutes: int = Field(ge=10, le=600)


class LLMPlan(BaseModel):
    tasks: list[LLMPlanTask] = Field(min_length=1, max_length=120)
