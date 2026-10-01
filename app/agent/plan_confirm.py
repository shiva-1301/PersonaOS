"""A "yes" to a study plan the assistant proposed is carried out in code.

qwen2.5:7b often discusses a schedule, gets "yes", and then writes the plan as text
(or claims it made one) without calling generate_study_plan (2 of 3 replays of a
real conversation). So when the user's message is a clear yes and the assistant's
previous message was about a study plan or schedule, one narrow JSON call reads the
conversation and returns what was agreed: the goal, its date, hours per week and
the days/times. Code validates that, then creates the goal if needed and the plan
(tools.plan_by_reference), before the agent runs. The agent then only describes it.

Nothing happens unless the model says the yes is for the plan and the values are
valid; then the agent handles the turn as before.
"""

import logging
import re
from datetime import date, datetime

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.confirmations import is_clear_yes
from app.agent.prompts import upcoming_dates
from app.agent.tools import ToolError, plan_by_reference
from app.db.models import Goal, Task, User
from app.services.llm import invoke_with_backoff
from app.services.planner_service import _extract_json

logger = logging.getLogger(__name__)

CONFIRM_MARKER = "### PERSONAOS PLAN CONFIRMATION ###"
# The assistant's previous message has to be about a plan for a yes to count.
_PLAN_TALK = re.compile(r"\b(study plan|plan|schedule|sessions?)\b", re.IGNORECASE)
HISTORY_MESSAGES = 12

SYSTEM = f"""{CONFIRM_MARKER}
You read the end of a conversation between a user and their study assistant.
Decide whether the user's LAST message agrees to create the study plan that the
assistant proposed in its previous message. Reply with ONLY this JSON object:
{{"confirmed": true or false,
  "goal": "short name of what they study for, e.g. English exam",
  "end_date": "YYYY-MM-DD (the exam or deadline date) or null",
  "hours_per_week": number or null,
  "preferences": "days and times agreed on, e.g. Mon 14:00-16:00, Wed 15:00-17:00, or null"}}
Rules:
- confirmed is true only if the last message says yes to making that study plan, not to
  something else (reminders, calendar events, a question).
- Use only facts stated in the conversation; null when something was never stated.
- Convert dates with the table below."""


class PlanConfirmation(BaseModel):
    confirmed: bool
    goal: str | None = Field(default=None, max_length=200)
    end_date: date | None = None
    hours_per_week: float | None = Field(default=None, gt=0, le=80)
    preferences: str | None = Field(default=None, max_length=1000)

    @field_validator("goal", "preferences", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return v.strip() or None if isinstance(v, str) else v


def _transcript(history: list[BaseMessage], message: str) -> str:
    lines = []
    for m in history[-HISTORY_MESSAGES:]:
        if isinstance(m, HumanMessage):
            lines.append(f"User: {m.content}")
        elif isinstance(m, AIMessage) and m.content:
            lines.append(f"Assistant: {m.content}")
    lines.append(f"User: {message}")
    return "\n\n".join(lines)


def _wants_check(history: list[BaseMessage], message: str) -> bool:
    if not is_clear_yes(message):
        return False
    previous = next((m for m in reversed(history) if isinstance(m, AIMessage) and m.content), None)
    return previous is not None and bool(_PLAN_TALK.search(str(previous.content)))


def read_confirmation(
    services, history: list[BaseMessage], message: str, now: datetime
) -> PlanConfirmation | None:
    prompt = [
        SystemMessage(f"{SYSTEM}\n\n{upcoming_dates(now, days=60)}"),
        HumanMessage(_transcript(history, message)),
    ]
    try:
        raw = invoke_with_backoff(
            services.planner_model,
            prompt,
            attempts=services.settings.LLM_RATE_LIMIT_ATTEMPTS,
        ).content
        return PlanConfirmation.model_validate_json(_extract_json(str(raw)))
    except (ValidationError, ValueError) as exc:
        logger.info("Plan confirmation unreadable", extra={"error": type(exc).__name__})
        return None


def _already_planned(db: Session, user: User, title: str) -> bool:
    """Don't plan twice: a goal by that name that already has tasks keeps them."""
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    for goal in db.scalars(select(Goal).where(Goal.user_id == user.id)):
        same = words and words <= set(re.findall(r"[a-z0-9]+", goal.title.lower()))
        if same and db.scalar(select(Task.id).where(Task.goal_id == goal.id).limit(1)):
            return True
    return False


def plan_if_confirmed(
    db: Session,
    user: User,
    services,
    history: list[BaseMessage],
    message: str,
    config: RunnableConfig,
    now: datetime,
) -> dict | None:
    """The plan's tool-style result, {"goal", "error"} if planning failed, or None
    when this turn isn't a confirmed plan (the agent handles it as usual)."""
    if not _wants_check(history, message):
        return None
    agreed = read_confirmation(services, history, message, now)
    if not (agreed and agreed.confirmed and agreed.goal and agreed.hours_per_week):
        return None
    if _already_planned(db, user, agreed.goal):
        return None
    try:
        result = plan_by_reference(
            db,
            user,
            services,
            config,
            agreed.goal,
            agreed.hours_per_week,
            end_date=agreed.end_date.isoformat() if agreed.end_date else None,
            preferences=agreed.preferences,
        )
    except ToolError as exc:
        logger.info("Confirmed plan could not be created")
        return {"goal": agreed.goal, "error": str(exc)}
    logger.info(
        "Study plan created from a confirmation", extra={"sessions": result["sessions_created"]}
    )
    return {"goal": agreed.goal, **result}


LISTED_SESSIONS = 12


def reply_text(result: dict, timezone: str) -> str:
    """The reply for a bare yes: the sessions that were really saved, not the model's
    retelling of its own proposal (which got dates and weekdays wrong)."""
    n = result["sessions_created"]
    span = f"{n} session{'s' if n != 1 else ''} from {result['first']} to {result['last']}"
    if result.get("goal_created"):
        head = f'Done: I added "{result["goal"]}" as a goal and made its study plan, {span}.'
    else:
        head = f'Done: your study plan for "{result["goal"]}" is ready, {span}.'
    lines = [head, ""]
    lines += [
        f"- {s['due']} · {s['title']} ({s['minutes']} min)"
        for s in result["sessions"][:LISTED_SESSIONS]
    ]
    if n > LISTED_SESSIONS:
        lines.append(f"- …and {n - LISTED_SESSIONS} more on the Goals & Tasks page.")
    if result.get("adjustments"):
        lines += ["", "Note: " + " ".join(result["adjustments"])]
    lines += ["", f"(Times are in {timezone}. Tick sessions off on the Goals & Tasks page.)"]
    return "\n".join(lines)
