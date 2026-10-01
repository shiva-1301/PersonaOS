"""Google Calendar for one user at a time: connection, encrypted tokens, events.

- Refresh tokens are Fernet-encrypted with TOKEN_ENCRYPTION_KEY before they touch the DB
  and are never logged. Access tokens live only in memory (per user, until expiry).
- OAuth `state` is single-use, expires after 10 minutes, and is bound to the verified user
  who started the flow: a random nonce stored server-side (oauth_states) plus a
  Fernet-encrypted payload naming that user; the callback must match both.
- Events are only ever created from a confirmed proposal (see confirm_proposal).
- Every method takes the user from the caller (verified token / agent config), never
  from model output, and every query filters on it.
"""

import json
import logging
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import CalendarProposal, ChatMessage, GoogleToken, OAuthState
from app.services.google_client import GoogleClient, ReconnectRequired
from app.services.time_utils import user_zone

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600


class GoogleNotConfigured(Exception):
    pass


class NotConnected(Exception):
    pass


class InvalidState(Exception):
    pass


class ProposalError(Exception):
    """Proposal missing, not the user's, expired or already decided (message is safe)."""


def google_configured(settings: Settings) -> bool:
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        return False
    if settings.TOKEN_ENCRYPTION_KEY is None:
        return False
    try:
        Fernet(settings.TOKEN_ENCRYPTION_KEY.get_secret_value().encode())
    except (ValueError, TypeError):
        logger.error("TOKEN_ENCRYPTION_KEY is not a valid Fernet key; Google integration off")
        return False
    return True


class GoogleService:
    def __init__(self, settings: Settings, client: GoogleClient | None = None):
        if not google_configured(settings):
            raise GoogleNotConfigured
        self.settings = settings
        self._fernet = Fernet(settings.TOKEN_ENCRYPTION_KEY.get_secret_value().encode())
        self.client = client or GoogleClient(
            settings.GOOGLE_CLIENT_ID,
            settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            settings.GOOGLE_REDIRECT_URI,
        )
        self._access: dict[uuid.UUID, tuple[str, float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ crypto

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    # ------------------------------------------------------------------ OAuth

    def start(self, db: Session, user_id: uuid.UUID, now: datetime | None = None) -> str:
        """Authorization URL with a fresh state bound to this user."""
        now = now or datetime.now(UTC)
        db.execute(delete(OAuthState).where(OAuthState.expires_at < now))  # housekeeping
        nonce = secrets.token_urlsafe(32)
        db.add(
            OAuthState(
                nonce=nonce, user_id=user_id, expires_at=now + timedelta(seconds=STATE_TTL_SECONDS)
            )
        )
        db.commit()
        payload = json.dumps({"n": nonce, "u": str(user_id)}).encode()
        return self.client.authorization_url(self._fernet.encrypt(payload).decode())

    def consume_state(self, db: Session, state: str, now: datetime | None = None) -> uuid.UUID:
        """Validate and burn the state; returns the user who started the flow."""
        now = now or datetime.now(UTC)
        try:
            data = json.loads(self._fernet.decrypt(state.encode(), ttl=STATE_TTL_SECONDS))
            nonce, user_id = data["n"], uuid.UUID(data["u"])
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise InvalidState("This sign-in link is invalid or has expired.") from None
        row = db.get(OAuthState, nonce)
        if row is None or row.user_id != user_id or row.expires_at < now:
            raise InvalidState("This sign-in link is invalid, expired or already used.")
        db.delete(row)  # single use
        db.commit()
        return user_id

    def complete(self, db: Session, user_id: uuid.UUID, code: str) -> list[str]:
        tokens = self.client.exchange_code(code)
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise ReconnectRequired("Google did not return offline access; please try again")
        scopes = sorted(set((tokens.get("scope") or "").split()))
        row = db.get(GoogleToken, user_id)
        if row is None:
            row = GoogleToken(user_id=user_id, encrypted_refresh_token="", scopes="")
            db.add(row)
        row.encrypted_refresh_token = self.encrypt(refresh)
        row.scopes = " ".join(scopes)
        row.needs_reconnect = False
        db.commit()
        self._remember_access(user_id, tokens)
        logger.info("Google connected", extra={"scopes": len(scopes)})
        return scopes

    def status(self, db: Session, user_id: uuid.UUID) -> dict:
        row = db.get(GoogleToken, user_id)
        return {
            "configured": True,
            "connected": row is not None and not row.needs_reconnect,
            "needs_reconnect": bool(row and row.needs_reconnect),
            "scopes": row.scopes.split() if row else [],
        }

    def disconnect(self, db: Session, user_id: uuid.UUID) -> bool:
        """Revoke at Google (best effort) and delete the stored token."""
        row = db.get(GoogleToken, user_id)
        with self._lock:
            self._access.pop(user_id, None)
        if row is None:
            return False
        revoked = False
        try:
            revoked = self.client.revoke(self.decrypt(row.encrypted_refresh_token))
        except InvalidToken:
            logger.warning("Stored Google token could not be decrypted; deleting it")
        db.delete(row)
        db.commit()
        logger.info("Google disconnected", extra={"revoked_at_google": revoked})
        return True

    # ------------------------------------------------------------------ tokens

    def _remember_access(self, user_id: uuid.UUID, tokens: dict) -> None:
        if tokens.get("access_token"):
            expires = time.monotonic() + int(tokens.get("expires_in", 3600)) - 60
            with self._lock:
                self._access[user_id] = (tokens["access_token"], expires)

    def access_token(self, db: Session, user_id: uuid.UUID) -> str:
        with self._lock:
            cached = self._access.get(user_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        row = db.get(GoogleToken, user_id)
        if row is None:
            raise NotConnected
        if row.needs_reconnect:
            raise ReconnectRequired("Google access expired or was revoked")
        try:
            tokens = self.client.refresh(self.decrypt(row.encrypted_refresh_token))
        except ReconnectRequired:
            row.needs_reconnect = True  # 7-day expiry in Testing mode, or revoked
            db.commit()
            logger.info("Google token needs reconnect")
            raise
        except InvalidToken:
            row.needs_reconnect = True
            db.commit()
            raise ReconnectRequired(
                "Stored Google access is unreadable; please reconnect"
            ) from None
        self._remember_access(user_id, tokens)
        return tokens["access_token"]

    def _call(self, db: Session, user_id: uuid.UUID, fn):
        """Run fn(access_token); on a 401 drop the cached token and retry once."""
        try:
            return fn(self.access_token(db, user_id))
        except ReconnectRequired:
            with self._lock:
                had_cache = self._access.pop(user_id, None) is not None
            if not had_cache:
                raise
            return fn(self.access_token(db, user_id))

    # ------------------------------------------------------------------ events

    def list_events(
        self, db: Session, user_id: uuid.UUID, start: datetime, end: datetime, limit: int = 20
    ) -> list[dict]:
        data = self._call(
            db,
            user_id,
            lambda token: self.client.list_events(token, start.isoformat(), end.isoformat(), limit),
        )
        return [
            {
                "title": e.get("summary") or "(no title)",
                "start": (e.get("start") or {}).get("dateTime")
                or (e.get("start") or {}).get("date"),
                "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
                "link": e.get("htmlLink"),
            }
            for e in data.get("items", [])
        ]

    def propose(
        self,
        db: Session,
        user_id: uuid.UUID,
        title: str,
        start: datetime,
        end: datetime,
        timezone: str,
        description: str | None = None,
        session_id: uuid.UUID | None = None,
    ) -> CalendarProposal:
        proposal = CalendarProposal(
            user_id=user_id,
            session_id=session_id,
            # Same clock as chat message timestamps, which awaiting_answer compares with.
            created_at=datetime.now(UTC),
            title=title.strip()[:200] or "Event",
            description=description,
            start_at=start,
            end_at=end,
            timezone=timezone,
        )
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    def pending(self, db: Session, user_id: uuid.UUID) -> list[CalendarProposal]:
        return list(
            db.scalars(
                select(CalendarProposal)
                .where(CalendarProposal.user_id == user_id, CalendarProposal.status == "pending")
                .order_by(CalendarProposal.created_at)
            )
        )

    def awaiting_answer(
        self, db: Session, user_id: uuid.UUID, session_id: uuid.UUID, before: datetime
    ) -> list[CalendarProposal]:
        """Pending proposals the assistant made in this chat's PREVIOUS turn: the ones the
        user's current message (asked at `before`) is answering. Older proposals, other
        chats' and this turn's own are excluded."""
        last_user_message = db.scalar(
            select(func.max(ChatMessage.created_at)).where(
                ChatMessage.session_id == session_id,
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
                ChatMessage.created_at < before,
            )
        )
        if last_user_message is None:
            return []
        return list(
            db.scalars(
                select(CalendarProposal)
                .where(
                    CalendarProposal.user_id == user_id,
                    CalendarProposal.session_id == session_id,
                    CalendarProposal.status == "pending",
                    CalendarProposal.created_at >= last_user_message,
                    CalendarProposal.created_at < before,
                )
                .order_by(CalendarProposal.start_at)
            )
        )

    def get_proposal(
        self, db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> CalendarProposal:
        row = db.scalar(
            select(CalendarProposal).where(
                CalendarProposal.id == proposal_id, CalendarProposal.user_id == user_id
            )
        )
        if row is None:
            raise ProposalError("Event proposal not found")
        return row

    def confirm(self, db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID) -> CalendarProposal:
        """Create the proposed event in Google Calendar (once)."""
        proposal = self.get_proposal(db, user_id, proposal_id)
        if proposal.status != "pending":
            raise ProposalError(f"This event proposal was already {proposal.status}")
        tz = user_zone(proposal.timezone)
        body = {
            "summary": proposal.title,
            "description": proposal.description or "Created by PersonaOS",
            "start": {"dateTime": proposal.start_at.astimezone(tz).isoformat(), "timeZone": tz.key},
            "end": {"dateTime": proposal.end_at.astimezone(tz).isoformat(), "timeZone": tz.key},
        }
        event = self._call(db, user_id, lambda token: self.client.insert_event(token, body))
        proposal.status = "created"
        proposal.event_id = event.get("id")
        proposal.event_link = event.get("htmlLink")
        proposal.decided_at = datetime.now(UTC)
        db.commit()
        logger.info("Calendar event created")
        return proposal

    def cancel(self, db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID) -> CalendarProposal:
        proposal = self.get_proposal(db, user_id, proposal_id)
        if proposal.status != "pending":
            raise ProposalError(f"This event proposal was already {proposal.status}")
        proposal.status, proposal.decided_at = "cancelled", datetime.now(UTC)
        db.commit()
        return proposal
