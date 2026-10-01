"""Code-level guard for clear study-plan requests (no model decision involved).

qwen2.5:7b sometimes answers "make me a study plan for X, 6 hours a week" by asking for
dates the planner doesn't need (it starts today and ends on the goal's target date),
even when told not to. When a message is unambiguously such a request (an action word,
"study plan", hours per week, exactly one matching goal of this user, and no dates,
days or other scheduling details that the model should handle), the runner creates the
plan in code before the model runs. The model then only describes the result and is
blocked from creating a second plan for that goal in the same message.

Anything less clear goes to the model unchanged.
"""

import logging
import re

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools import ToolError, plan_for_goal
from app.db.models import Goal, User

logger = logging.getLogger(__name__)

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}
_HOURS = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*(?:h|hrs?|hours?)\b"
    r"\s*(?:a|an|per|each|every|/|of\s+study\s+(?:a|per))?\s*week\b"
    r"|\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*(?:h|hrs?|hours?)\s+weekly\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(make|create|generate|build|set\s+up|draw\s+up|prepare|give\s+me|plan\s+out|"
    r"i\s+(?:want|need)|can\s+you|could\s+you|please)\b",
    re.IGNORECASE,
)
_STUDY_PLAN = re.compile(r"\bstudy[\s-]*(?:plan|schedule)\b", re.IGNORECASE)
_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|not|no|never|cancel|delete|remove|stop|instead)\b", re.IGNORECASE
)
# Scheduling details the planner's defaults would ignore: leave those to the model.
_DETAILS = re.compile(
    r"\b(from|starting|start|until|till|by|before|after|between|next|tomorrow|today|"
    r"tonight|weekends?|weekdays?|mornings?|evenings?|afternoons?|nights?|"
    r"mon(day)?|tue(s(day)?)?|wed(nesday)?|thu(rs(day)?)?|fri(day)?|sat(urday)?|sun(day)?|"
    r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|"
    r"sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?|am|pm)\b"
    r"|\d{1,2}[:/-]\d{1,2}",  # 18:00, 12/10, 10-12 (not decimals like 4.5)
    re.IGNORECASE,
)
_TITLE_FILLER = {
    "a", "an", "the", "my", "to", "of", "for", "and", "in", "on", "goal",
    "finish", "complete", "completing", "finishing", "learn", "learning", "study",
    "studying", "master", "pass", "do", "get", "become",
}  # fmt: skip
_WORD = re.compile(r"[a-z0-9+#]+")


def requested_hours(message: str) -> float | None:
    """Hours per week if `message` is clearly a request to make a study plan."""
    text = message or ""
    if not (_STUDY_PLAN.search(text) and _ACTION.search(text)):
        return None
    if _NEGATION.search(text) or _DETAILS.search(text):
        return None
    hours = _HOURS.search(text)
    if hours is None:
        return None
    raw = (hours.group(1) or hours.group(2)).lower()
    value = float(_NUMBER_WORDS.get(raw, raw))
    return value if 0 < value <= 80 else None


def _key_words(title: str) -> set[str]:
    return {w for w in _WORD.findall(title.lower()) if w not in _TITLE_FILLER}


def matching_goal(db: Session, user_id, message: str) -> Goal | None:
    """The one active goal of THIS user that the message refers to, else None.

    A goal matches when every key word of its title appears in the message. If no
    title matches and the user has exactly one active goal, that one is meant."""
    goals = list(db.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status == "active")))
    words = set(_WORD.findall((message or "").lower()))
    matches = [g for g in goals if _key_words(g.title) and _key_words(g.title) <= words]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(goals) == 1:
        return goals[0]
    return None


def plan_if_clearly_requested(
    db: Session, user: User, services, message: str, config: RunnableConfig
) -> dict | None:
    """Create the plan in code for a clear request. Returns the tool-style result, an
    {"error": ...} when planning failed, or None when the model should handle it."""
    hours = requested_hours(message)
    if hours is None:
        return None
    goal = matching_goal(db, user.id, message)
    if goal is None or goal.target_date is None:
        return None  # ambiguous, or the model must ask for an end date
    try:
        result = plan_for_goal(db, user, services, goal, config, hours)
    except ToolError as exc:
        logger.info("Study-plan guard: planning failed")
        return {"goal": goal.title, "error": str(exc)}
    logger.info("Study plan created by the guard", extra={"sessions": result["sessions_created"]})
    return result


def system_note(result: dict) -> str:
    if "error" in result:
        return (
            f'The user asked for a study plan for "{result["goal"]}". It was attempted in '
            f"code and failed: {result['error']}. Tell them it did not work and why. Do not "
            "call generate_study_plan again for it in this message."
        )
    sessions = "; ".join(
        f"{s['due']} {s['title']} ({s['minutes']} min)" for s in result["sessions"]
    )
    created = f"{result['goal_created']}, created just now. " if result.get("goal_created") else ""
    return (
        created
        + f'The study plan the user asked for was ALREADY created for goal "{result["goal"]}": '
        f"{result['sessions_created']} sessions from {result['first']} to {result['last']}. "
        f"Sessions: {sessions}. Adjustments: {result['adjustments'] or 'none'}. "
        "Summarise this plan for the user briefly, using only these facts. Do not call "
        "generate_study_plan for this goal again and do not ask for dates."
    )
