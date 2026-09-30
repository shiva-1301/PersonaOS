"""Study-plan generation: LLM proposes sessions as JSON, code enforces the rules.

1. Validate the window (default: today .. goal.target_date).
2. Recall remembered preferences ("studies best after 6 pm") and merge request preferences.
3. Ask the LLM (JSON mode) for {"tasks": [...]}; validate with Pydantic; on invalid output
   retry up to 2 more times, telling the model what was wrong; then fail readably.
4. Deterministic post-processing (normalise_plan): clamp into the window, no past times,
   cap each calendar week at hours_per_week, move overflow to later weeks, drop the rest.
5. Save every task, linked to the goal, in one transaction.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.prompts import PLAN_MARKER
from app.db.models import Goal, Task, User
from app.schemas.planner import LLMPlan, LLMPlanTask, PlanRequest
from app.services.container import Services
from app.services.llm import invoke_with_backoff
from app.services.time_utils import local_midnight, to_utc, user_zone, week_start

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # first try + 2 retries
MAX_WINDOW_DAYS = 366

SYSTEM_PROMPT = f"""\
You are the {PLAN_MARKER}. You turn one goal into dated study sessions.

Return ONLY a JSON object, no prose and no markdown:
{{"tasks": [{{"title": str, "notes": str, "due_at": "YYYY-MM-DDTHH:MM", "est_minutes": int}}]}}

Rules:
- due_at is a LOCAL date-time in the user's timezone, inside the window, never in the past.
- Each session is 30-120 minutes (est_minutes).
- The minutes in any calendar week (Monday-Sunday) must not exceed the weekly budget.
- Spread sessions evenly across the whole window; build from fundamentals to review.
- Respect the user's preferences (for example preferred times of day) when choosing times.
- Titles are specific and actionable, numbered by session (not by week), e.g. \
"Session 4: gradient descent - worked examples".
- Put what to do in notes (one or two sentences)."""


class PlanWindowError(Exception):
    """The request cannot be planned (bad or empty window). Message is user-facing."""


class PlanGenerationError(Exception):
    """The LLM did not return a valid plan after MAX_ATTEMPTS."""


@dataclass(frozen=True)
class PlannedSession:
    title: str
    notes: str | None
    due_at: datetime  # UTC
    est_minutes: int


@dataclass(frozen=True)
class PlanResult:
    tasks: list[Task]
    adjustments: list[str]
    used_preferences: list[str]


# --------------------------------------------------------------------------- window


def plan_window(
    goal: Goal, req: PlanRequest, now: datetime, tz: ZoneInfo
) -> tuple[date, date, datetime, datetime]:
    """(start, end, earliest, latest): dates, and the UTC instants sessions must fall in."""
    today = now.astimezone(tz).date()
    start = req.start_date or today
    end = req.end_date or goal.target_date
    if end is None:
        raise PlanWindowError("end_date is required because the goal has no target date")
    if end < today:
        raise PlanWindowError("The plan must end today or later")
    start = max(start, today)
    if end < start:
        raise PlanWindowError("end_date must not be before start_date")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise PlanWindowError(f"Plans can cover at most {MAX_WINDOW_DAYS} days")
    earliest = max(local_midnight(start, tz).astimezone(UTC), now)
    latest = (local_midnight(end + timedelta(days=1), tz) - timedelta(minutes=1)).astimezone(UTC)
    if latest <= earliest:
        raise PlanWindowError("There is no time left in this plan window")
    return start, end, earliest, latest


# --------------------------------------------------------------------------- LLM


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    first, last = text.find("{"), text.rfind("}")
    return text[first : last + 1] if first != -1 and last > first else text


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(p) for p in err["loc"])
            parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
        return "; ".join(parts)
    return str(exc)[:300]


def request_plan(model, messages: list[BaseMessage], attempts: int, rate_attempts: int) -> LLMPlan:
    """Call the model, validate, and retry with the error appended."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        result = invoke_with_backoff(model, messages, attempts=rate_attempts)
        raw = result.content if isinstance(result.content, str) else json.dumps(result.content)
        try:
            return LLMPlan.model_validate_json(_extract_json(raw))
        except (ValidationError, ValueError) as exc:
            last_error = _describe(exc)
            logger.info("Invalid plan from LLM", extra={"attempt": attempt, "error": last_error})
            messages = [
                *messages,
                AIMessage(raw[:4000]),
                HumanMessage(
                    f"That output was invalid: {last_error}. Reply again with ONLY the "
                    'corrected JSON object {"tasks": [...]} following every rule.'
                ),
            ]
    raise PlanGenerationError(last_error)


# --------------------------------------------------------------------------- rules


def _shift_into_window(due: datetime, earliest: datetime, latest: datetime, tz) -> datetime:
    """Keep the local time of day, move the date into [earliest, latest]."""
    local = due.astimezone(tz)
    if due < earliest:
        day = earliest.astimezone(tz).date()
        candidate = datetime.combine(day, local.time(), tzinfo=tz).astimezone(UTC)
        if candidate < earliest:
            candidate += timedelta(days=1)
        due = candidate
    if due > latest:
        day = latest.astimezone(tz).date()
        due = min(datetime.combine(day, local.time(), tzinfo=tz).astimezone(UTC), latest)
    return due


def normalise_plan(
    proposed: list[LLMPlanTask],
    *,
    earliest: datetime,
    latest: datetime,
    tz: ZoneInfo,
    weekly_budget_minutes: int,
) -> tuple[list[PlannedSession], list[str]]:
    """Enforce window, no-past and weekly-budget rules. Pure: easy to test."""
    moved_in = clamped = moved_later = dropped = 0
    sessions: list[PlannedSession] = []
    for item in proposed:
        due = to_utc(item.due_at, tz)
        fixed = _shift_into_window(due, earliest, latest, tz)
        if fixed != due:
            moved_in += 1
        minutes = item.est_minutes
        if minutes > weekly_budget_minutes:
            minutes = weekly_budget_minutes
            clamped += 1
        sessions.append(PlannedSession(item.title.strip(), item.notes, fixed, minutes))

    sessions.sort(key=lambda s: s.due_at)
    used: dict[date, int] = {}
    kept: list[PlannedSession] = []
    for s in sessions:
        due = s.due_at
        while (
            due <= latest
            and used.get(week_start(due, tz), 0) + s.est_minutes > weekly_budget_minutes
        ):
            due += timedelta(days=7)
        if due > latest:
            dropped += 1
            continue
        if due != s.due_at:
            moved_later += 1
        used[week_start(due, tz)] = used.get(week_start(due, tz), 0) + s.est_minutes
        kept.append(PlannedSession(s.title, s.notes, due, s.est_minutes))

    notes = []
    if moved_in:
        notes.append(
            f"Moved {moved_in} session(s) that fell outside the plan window or in the past."
        )
    if clamped:
        notes.append(f"Shortened {clamped} session(s) longer than the weekly budget.")
    if moved_later:
        notes.append(
            f"Moved {moved_later} session(s) to a later week to respect the weekly budget."
        )
    if dropped:
        notes.append(
            f"Dropped {dropped} session(s) that did not fit the weekly budget before the end date."
        )
    kept.sort(key=lambda s: s.due_at)
    return kept, notes


# --------------------------------------------------------------------------- entry point


def _preferences(db: Session, services: Services, user: User, goal: Goal, extra: str | None):
    remembered: list[str] = []
    try:
        hits = services.memory.recall(
            db, user.id, f"study schedule preferences, best time of day, {goal.title}", k=5
        )
        remembered = [h.text for h in hits]
    except Exception as exc:
        logger.warning("Preference recall failed", extra={"error": type(exc).__name__})
    return remembered + ([extra.strip()] if extra and extra.strip() else [])


def generate_study_plan(
    db: Session,
    services: Services,
    user: User,
    goal: Goal,
    req: PlanRequest,
    *,
    now: datetime | None = None,
) -> PlanResult:
    now = now or datetime.now(UTC)
    tz = user_zone(user.timezone)
    start, end, earliest, latest = plan_window(goal, req, now, tz)
    budget = int(round(req.hours_per_week * 60))
    preferences = _preferences(db, services, user, goal, req.preferences)

    lines = [
        f"Goal: {goal.title}",
        f"Description: {goal.description or '-'}",
        f"Window: {start.isoformat()} to {end.isoformat()}",
        f"Now: {now.astimezone(tz).strftime('%Y-%m-%dT%H:%M')} ({tz.key})",
        f"Weekly budget: {budget} minutes",
        "Preferences:",
        *([f"- {p}" for p in preferences] or ["- none given"]),
    ]
    messages: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT), HumanMessage("\n".join(lines))]
    plan = request_plan(
        services.planner_model,
        messages,
        attempts=MAX_ATTEMPTS,
        rate_attempts=services.settings.LLM_RATE_LIMIT_ATTEMPTS,
    )
    sessions, adjustments = normalise_plan(
        plan.tasks, earliest=earliest, latest=latest, tz=tz, weekly_budget_minutes=budget
    )
    if not sessions:
        raise PlanGenerationError("no session fits the window and weekly budget")

    tasks = [
        Task(
            id=uuid.uuid4(),
            user_id=user.id,
            goal_id=goal.id,
            title=s.title[:200],
            notes=s.notes,
            due_at=s.due_at,
            est_minutes=s.est_minutes,
            status="todo",
        )
        for s in sessions
    ]
    db.add_all(tasks)  # one transaction: all tasks or none
    db.commit()
    for t in tasks:
        db.refresh(t)
    logger.info(
        "Study plan created",
        extra={"sessions": len(tasks), "adjustments": len(adjustments)},
    )
    return PlanResult(tasks=tasks, adjustments=adjustments, used_preferences=preferences)
