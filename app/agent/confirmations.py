"""Deterministic "yes" handling for calendar proposals (no model decision involved).

When the assistant proposed events in the previous turn of a chat and the user's next
message there is a clear yes, the runner confirms those proposals in code before the
model runs. A small model can't then forget the proposal, ask for the details again,
or skip the tool. Proposals made in the same message are never confirmed by it.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import CalendarProposal, User
from app.services.google_client import GoogleError, ReconnectRequired
from app.services.google_service import NotConnected, ProposalError
from app.services.time_utils import user_zone

logger = logging.getLogger(__name__)

_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|confirm(ed)?|go ahead|do it|please do|create it|"
    r"add it|book it|schedule it|sounds good|correct|that's right|perfect|great)\b",
    re.IGNORECASE,
)
_NO = re.compile(r"\b(no|not|don'?t|cancel|stop|wait|change|instead|but|wrong)\b", re.IGNORECASE)

# A yes this short is the whole message: reply from a template, no model call.
# Longer messages ("yes, and also add a task ...") still go to the agent afterwards.
BARE_YES_MAX_WORDS = 8


def is_clear_yes(text: str) -> bool:
    return bool(_YES.match(text or "")) and not _NO.search(text or "")


def is_bare_yes(text: str) -> bool:
    return is_clear_yes(text) and len((text or "").split()) <= BARE_YES_MAX_WORDS


@dataclass
class Outcome:
    title: str
    start: str  # local, for the reply
    end: str
    created: bool
    error: str | None = None


def _when(p: CalendarProposal, tz) -> tuple[str, str]:
    start, end = p.start_at.astimezone(tz), p.end_at.astimezone(tz)
    end_fmt = "%H:%M" if start.date() == end.date() else "%a %d %b %H:%M"
    return start.strftime("%a %d %b %H:%M"), end.strftime(end_fmt)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, NotConnected):
        return "Google Calendar is not connected. Connect it in Integrations"
    if isinstance(exc, ReconnectRequired):
        return "Google access expired or was revoked. Reconnect Google Calendar"
    if isinstance(exc, ProposalError):
        return str(exc)
    return "Google Calendar didn't respond. Please try again in a moment"


def confirm_on_yes(
    db: Session,
    user: User,
    session_id: uuid.UUID,
    message: str,
    asked_at: datetime,
    google,
) -> list[Outcome]:
    """Confirm the previous turn's proposals if `message` is a clear yes; else []."""
    if google is None or not is_clear_yes(message):
        return []
    proposals = google.awaiting_answer(db, user.id, session_id, before=asked_at)
    tz = user_zone(user.timezone)
    outcomes = []
    for p in proposals:
        start, end = _when(p, tz)
        try:
            google.confirm(db, user.id, p.id)
            outcomes.append(Outcome(p.title, start, end, created=True))
        except (NotConnected, GoogleError, ProposalError) as exc:
            db.rollback()
            outcomes.append(Outcome(p.title, start, end, created=False, error=_error_text(exc)))
    if outcomes:
        logger.info(
            "Calendar proposals confirmed by a yes",
            extra={"events_created": sum(o.created for o in outcomes), "total": len(outcomes)},
        )
    return outcomes


def reply_text(outcomes: list[Outcome], timezone: str) -> str:
    lines = []
    for o in outcomes:
        if o.created:
            lines.append(f'Done: "{o.title}" is in your Google Calendar, {o.start}-{o.end}.')
        else:
            lines.append(f'I couldn\'t add "{o.title}": {o.error}, then ask me again.')
    if any(o.created for o in outcomes):
        lines.append(f"(Times are in {timezone}.)")
    return "\n".join(lines)


def system_note(outcomes: list[Outcome]) -> str:
    """For a longer yes that the agent still answers: what already happened in code."""
    done = "; ".join(
        f'"{o.title}" {o.start}-{o.end}: '
        + ("created in Google Calendar" if o.created else f"NOT created ({o.error})")
        for o in outcomes
    )
    return (
        "The user's 'yes' already confirmed the calendar proposal(s) from your previous "
        f"message: {done}. Tell them the outcome. Do not propose or confirm these again."
    )
