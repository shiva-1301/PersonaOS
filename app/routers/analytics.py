"""Dashboard numbers: completions over time, streak, task counts, goal progress, memory."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.schemas.analytics import AnalyticsSummary
from app.services.analytics_service import summary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(
    user: CurrentUser,
    db: DbSession,
    days: Annotated[int, Query(ge=7, le=90, description="days of daily completions")] = 30,
    weeks: Annotated[int, Query(ge=4, le=52, description="weeks of weekly completions")] = 12,
) -> dict:
    """Your numbers, in your timezone: tasks completed per day and per week, the current
    and longest daily streak, open / overdue / due-soon task counts, progress per goal,
    and memories by state."""
    return summary(db, user, days=days, weeks=weeks)
