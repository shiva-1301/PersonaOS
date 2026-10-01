"""Deterministic corrections to the assistant's reply text.

qwen2.5:7b pairs dates with the wrong weekday ("Monday, 2026-10-02" for a Friday),
even with a date table in its prompt. Where a weekday sits right next to an explicit
date, the weekday is recomputed from the date; nothing else in the text changes.
"""

import re
from datetime import date, timedelta

_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAY = (
    r"(?P<wd>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?"
    r"|sat(?:urday)?|sun(?:day)?)\b"
)
_MONTH = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"
_YEAR = r"(?:,?\s+(?P<year>\d{4}))?"
_PATTERNS = [
    # Fri 2026-10-02, Friday, (2026-10-02)
    re.compile(r"\b" + _WEEKDAY + r"(?P<sep>\.?,?\s*\(?)(?P<iso>\d{4}-\d{2}-\d{2})\b", re.I),
    # Friday, October 2(, 2026)
    re.compile(r"\b" + _WEEKDAY + r"\.?,?\s+" + _MONTH + r"\s+" + _DAY + _YEAR, re.I),
    # Friday, 2 October( 2026)
    re.compile(r"\b" + _WEEKDAY + r"\.?,?\s+" + _DAY + r"\s+" + _MONTH + _YEAR, re.I),
]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _date_of(match: re.Match, today: date) -> date | None:
    try:
        if match.groupdict().get("iso"):
            return date.fromisoformat(match["iso"])
        month = _MONTHS.index(match["month"][:3].lower()) + 1
        day = int(match["day"])
        if match["year"]:
            return date(int(match["year"]), month, day)
        guess = date(today.year, month, day)
        # No year: the nearest such date (a reply about "October 2" in December means next year)
        return guess if guess >= today - timedelta(days=180) else date(today.year + 1, month, day)
    except ValueError:
        return None


def _styled(name: str, like: str) -> str:
    """The right weekday, written the way the model wrote the wrong one."""
    word = name if len(like) > 4 or like.lower().endswith("day") else name[:3]
    if like.isupper():
        return word.upper()
    if like.islower():
        return word.lower()
    return word


# "I've created a goal…" / "has been added" (claims of something done, not plans like "I'll").
_CLAIM = re.compile(
    r"\b(?:I've|I have|I just)\s+(?:now\s+)?(?:created|added|made|scheduled|set up|saved|booked)\b"
    r"|\b(?:has|have)\s+been\s+(?:created|added|made|scheduled|set up|saved|booked)\b",
    re.I,
)
_FAILED = {
    "generate_study_plan": "make the study plan",
    "create_goal": "create the goal",
    "add_task": "add the task",
    "update_task": "update the task",
    "create_calendar_event": "propose the calendar event",
    "confirm_calendar_event": "add the calendar event",
    "remember_explicit": "save that memory",
}


def flag_false_claims(text: str, tool_results: list[dict]) -> str:
    """When every tool in the turn failed but the reply claims something was done, add
    a correction. qwen2.5:7b wrote "I've created a goal" after a failed tool call in 2
    of 3 replays, even though the tool result says nothing changed."""
    failed = [r["tool"] for r in tool_results if not r["ok"]]
    if not failed or any(r["ok"] for r in tool_results) or not _CLAIM.search(text):
        return text
    what = " or ".join(dict.fromkeys(_FAILED.get(t, f"use {t}") for t in failed))
    return f"{text}\n\n(Correction: nothing was created or changed yet. I couldn't {what}.)"


def fix_weekdays(text: str, today: date) -> str:
    def repair(match: re.Match) -> str:
        when = _date_of(match, today)
        if when is None:
            return match[0]
        written = match["wd"]
        right = _FULL[when.weekday()]
        if right.lower().startswith(written[:3].lower()):
            return match[0]
        start = match.start("wd") - match.start()
        return match[0][:start] + _styled(right, written) + match[0][start + len(written) :]

    for pattern in _PATTERNS:
        text = pattern.sub(repair, text)
    return text
