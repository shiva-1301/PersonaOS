"""Google Calendar connection and event proposals.

Flow: the signed-in client calls GET /integrations/google/start and opens the returned
URL in a browser; Google redirects to /integrations/google/callback (no PersonaOS token
there: the single-use, user-bound `state` identifies the user).
"""

import html
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.deps import CurrentUser, DbSession
from app.services.google_client import GoogleError, GoogleUnavailable, ReconnectRequired
from app.services.google_service import InvalidState, NotConnected, ProposalError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/google", tags=["integrations"])


def _google(request: Request):
    google = request.app.state.services.google
    if google is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google integration is not configured"
        )
    return google


def google_http_error(exc: Exception) -> HTTPException:
    """Expired/revoked tokens and Google outages are clear 4xx/5xx, never a 500."""
    if isinstance(exc, NotConnected):
        return HTTPException(status.HTTP_409_CONFLICT, "Google Calendar is not connected")
    if isinstance(exc, ReconnectRequired):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            "Google access expired or was revoked. Please reconnect Google Calendar.",
        )
    if isinstance(exc, GoogleUnavailable):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    if isinstance(exc, ProposalError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND if "not found" in str(exc) else 409, str(exc)
        )
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


def _page(title: str, message: str, code: int = 200, back: str | None = None) -> HTMLResponse:
    link = (
        f"<p><a href='{html.escape(back, quote=True)}/integrations'>Back to PersonaOS</a></p>"
        if back
        else ""
    )
    body = (
        "<!doctype html><meta charset='utf-8'><title>PersonaOS</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{link}</body>"
    )
    return HTMLResponse(body, status_code=code)


@router.get("/start")
def start(request: Request, user: CurrentUser, db: DbSession) -> dict:
    """URL of Google's consent page for this user (valid for 10 minutes, single use)."""
    return {"authorization_url": _google(request).start(db, user.id)}


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    db: DbSession,
    state: str = Query(default=""),
    code: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    google = request.app.state.services.google
    back = request.app.state.settings.FRONTEND_ORIGIN.rstrip("/")
    if google is None:
        return _page("Not available", "Google integration is not configured.", 503)
    try:
        user_id = google.consume_state(db, state)  # the user who started the flow
    except InvalidState as exc:
        return _page("Link expired", str(exc) + " Start the connection again.", 400, back)
    if error or not code:
        return _page(
            "Not connected", "Google access was not granted. You can try again.", 400, back
        )
    try:
        google.complete(db, user_id, code)
    except GoogleError:
        logger.warning("Google code exchange failed")
        return _page(
            "Not connected", "Google could not complete the connection. Try again.", 502, back
        )
    return _page(
        "Google Calendar connected", "You can close this tab and return to PersonaOS.", back=back
    )


@router.get("/status")
def connection_status(request: Request, user: CurrentUser, db: DbSession) -> dict:
    google = request.app.state.services.google
    if google is None:
        return {"configured": False, "connected": False, "needs_reconnect": False, "scopes": []}
    return google.status(db, user.id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(request: Request, user: CurrentUser, db: DbSession) -> None:
    """Revoke PersonaOS's access at Google and delete the stored token."""
    if not _google(request).disconnect(db, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google Calendar is not connected")


@router.get("/events")
def upcoming_events(
    request: Request, user: CurrentUser, db: DbSession, days: int = Query(7, ge=1, le=60)
) -> list[dict]:
    now = datetime.now(UTC)
    try:
        return _google(request).list_events(db, user.id, now, now + timedelta(days=days))
    except (NotConnected, GoogleError) as exc:
        raise google_http_error(exc) from None


class ProposalOut(BaseModel):
    id: uuid.UUID
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    event_link: str | None = None


@router.get("/proposals", response_model=list[ProposalOut])
def pending_proposals(request: Request, user: CurrentUser, db: DbSession):
    """Events the assistant proposed that are waiting for your confirmation."""
    return _google(request).pending(db, user.id)


@router.post("/proposals/{proposal_id}/confirm", response_model=ProposalOut)
def confirm_proposal(proposal_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession):
    """Explicit user confirmation (e.g. a button): create the event in Google Calendar."""
    try:
        return _google(request).confirm(db, user.id, proposal_id)
    except (NotConnected, GoogleError, ProposalError) as exc:
        raise google_http_error(exc) from None


@router.post("/proposals/{proposal_id}/cancel", response_model=ProposalOut)
def cancel_proposal(proposal_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession):
    try:
        return _google(request).cancel(db, user.id, proposal_id)
    except ProposalError as exc:
        raise google_http_error(exc) from None
