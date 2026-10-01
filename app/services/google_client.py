"""Minimal Google OAuth 2.0 + Calendar REST client (httpx).

Secrets never appear in URLs (tokens go in POST bodies or the Authorization header), and
error messages raised from here never contain token values.
"""

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
# Create and read events on the user's calendars; nothing broader.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


class GoogleError(Exception):
    """Base class; messages are safe to show (no secrets, no raw responses)."""


class ReconnectRequired(GoogleError):
    """Refresh token expired/revoked (invalid_grant) or rejected: user must reconnect."""


class GoogleUnavailable(GoogleError):
    """Network problem or Google 5xx: try again later."""


class GoogleRequestError(GoogleError):
    """Google rejected the request (4xx other than auth)."""


class GoogleClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http=None):
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._http = http or httpx.Client(timeout=20)

    def authorization_url(self, state: str) -> str:
        return f"{AUTH_URL}?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": CALENDAR_SCOPE,
                "access_type": "offline",  # we need a refresh token
                "prompt": "consent",  # always issue one, also on reconnect
                "include_granted_scopes": "true",
                "state": state,
            }
        )

    def _post_token(self, data: dict) -> dict:
        try:
            resp = self._http.post(TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise GoogleUnavailable("Could not reach Google") from exc
        if resp.status_code >= 500:
            raise GoogleUnavailable("Google is temporarily unavailable")
        body = _json(resp)
        if resp.status_code == 400 and body.get("error") == "invalid_grant":
            raise ReconnectRequired("Google access expired or was revoked")
        if resp.status_code in (400, 401, 403):
            raise GoogleRequestError(f"Google rejected the request ({body.get('error', 'error')})")
        return body

    def exchange_code(self, code: str) -> dict:
        """-> {"refresh_token", "access_token", "expires_in", "scope", ...}"""
        return self._post_token(
            {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    def refresh(self, refresh_token: str) -> dict:
        """-> {"access_token", "expires_in", ...}; ReconnectRequired on invalid_grant."""
        return self._post_token(
            {
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
            }
        )

    def revoke(self, token: str) -> bool:
        """Best effort; the token goes in the POST body, never the URL."""
        try:
            resp = self._http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    def _calendar(self, method: str, access_token: str, **kwargs) -> dict:
        try:
            resp = self._http.request(
                method,
                EVENTS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise GoogleUnavailable("Could not reach Google Calendar") from exc
        if resp.status_code == 401:
            raise ReconnectRequired("Google access expired or was revoked")
        if resp.status_code >= 500:
            raise GoogleUnavailable("Google Calendar is temporarily unavailable")
        if resp.status_code >= 400:
            reason = (_json(resp).get("error") or {}).get("message", "request rejected")
            raise GoogleRequestError(f"Google Calendar rejected the request: {reason[:200]}")
        return _json(resp)

    def insert_event(self, access_token: str, event: dict) -> dict:
        return self._calendar("POST", access_token, json=event)

    def list_events(self, access_token: str, time_min: str, time_max: str, limit: int) -> dict:
        return self._calendar(
            "GET",
            access_token,
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": limit,
            },
        )


def _json(resp) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
