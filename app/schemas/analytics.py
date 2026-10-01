import uuid
from datetime import date

from pydantic import BaseModel


class DayCount(BaseModel):
    date: date
    completed: int


class WeekCount(BaseModel):
    week_start: date  # Monday, in the user's timezone
    completed: int


class GoalProgressOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    target_date: date | None
    done: int
    total: int
    ratio: float


class Streak(BaseModel):
    current: int  # consecutive days with a completed task, ending today (or yesterday)
    longest: int  # longest run within the last 365 days
    completed_today: bool


class TaskCounts(BaseModel):
    todo: int
    doing: int
    done: int
    overdue: int  # not done and due before now
    due_soon: int  # not done and due within the next DUE_SOON_HOURS


class AnalyticsSummary(BaseModel):
    timezone: str
    today: date
    due_soon_hours: int
    completions_per_day: list[DayCount]
    completions_per_week: list[WeekCount]
    completed_last_7_days: int
    streak: Streak
    tasks: TaskCounts
    goals: list[GoalProgressOut]
    memory_by_state: dict[str, int]
