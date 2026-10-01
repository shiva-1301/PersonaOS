"""Firebase email/password sign-in over the Firebase Auth REST API (no Streamlit code).

The Streamlit server signs the user in with the web API key (FIREBASE_WEB_API_KEY),
keeps the ID token and refresh token in the user's st.session_state only, and
exchanges the refresh token for a new ID token shortly before the old one expires.
Passwords and tokens are never logged or written anywhere.
"""

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

IDENTITY = "https://identitytoolkit.googleapis.com/v1/accounts"
SECURE_TOKEN = "https://securetoken.googleapis.com/v1/token"
# Refresh this many seconds before the ID token (1 hour) expires.
REFRESH_MARGIN = 120
# Deleting a login needs a sign-in within the last ~5 minutes (Firebase's rule).
RECENT_SIGN_IN = 270

_FRIENDLY = {
    "INVALID_LOGIN_CREDENTIALS": "Wrong email or password.",
    "INVALID_PASSWORD": "Wrong email or password.",
    "EMAIL_NOT_FOUND": "Wrong email or password.",
    "INVALID_EMAIL": "That email address doesn't look right.",
    "MISSING_PASSWORD": "Enter your password.",
    "EMAIL_EXISTS": "An account with this email already exists. Sign in instead.",
    "WEAK_PASSWORD": "Choose a password with at least 6 characters.",
    "USER_DISABLED": "This account has been disabled.",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many attempts. Wait a few minutes and try again.",
    "OPERATION_NOT_ALLOWED": "Email/password sign-in is not enabled in this Firebase project.",
    "TOKEN_EXPIRED": "Your session expired. Please sign in again.",
    "INVALID_REFRESH_TOKEN": "Your session expired. Please sign in again.",
    "USER_NOT_FOUND": "This account no longer exists.",
    "INVALID_ID_TOKEN": "Your session expired. Please sign in again.",
    "CREDENTIAL_TOO_OLD_LOGIN_AGAIN": "For your security, sign out and sign in again first.",
}


class AuthError(Exception):
    """A user-safe message; `reason` is Firebase's error code (e.g. EMAIL_EXISTS)."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.message, self.reason = message, reason


@dataclass(frozen=True)
class FirebaseSession:
    uid: str
    email: str
    id_token: str
    refresh_token: str
    expires_at: float  # epoch seconds

    def __repr__(self) -> str:  # never print tokens
        return f"FirebaseSession(uid={self.uid!r}, email={self.email!r})"


class FirebaseAuth:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not api_key:
            raise AuthError("Sign-in isn't configured: set FIREBASE_WEB_API_KEY in .env.")
        self._key = api_key
        self._http = httpx.Client(transport=transport, timeout=15)
        self._clock = clock

    def _post(self, url: str, **kwargs) -> dict:
        try:
            response = self._http.post(url, params={"key": self._key}, **kwargs)
        except httpx.TransportError:
            raise AuthError("Can't reach Firebase to sign in. Check your connection.") from None
        if response.status_code >= 400:
            try:
                reason = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                reason = ""
            code = reason.split(" ")[0].split(":")[0]  # "WEAK_PASSWORD : Password ..."
            raise AuthError(_FRIENDLY.get(code, "Sign-in failed. Please try again."), code)
        return response.json()

    def _session(self, body: dict) -> FirebaseSession:
        return FirebaseSession(
            uid=body["localId"],
            email=body.get("email", ""),
            id_token=body["idToken"],
            refresh_token=body["refreshToken"],
            expires_at=self._clock() + int(body.get("expiresIn", 3600)),
        )

    def sign_in(self, email: str, password: str) -> FirebaseSession:
        body = {"email": email.strip(), "password": password, "returnSecureToken": True}
        return self._session(self._post(f"{IDENTITY}:signInWithPassword", json=body))

    def sign_up(self, email: str, password: str) -> FirebaseSession:
        body = {"email": email.strip(), "password": password, "returnSecureToken": True}
        return self._session(self._post(f"{IDENTITY}:signUp", json=body))

    def refresh(self, session: FirebaseSession) -> FirebaseSession:
        body = self._post(
            SECURE_TOKEN,
            data={"grant_type": "refresh_token", "refresh_token": session.refresh_token},
        )
        return FirebaseSession(
            uid=body.get("user_id", session.uid),
            email=session.email,
            id_token=body["id_token"],
            refresh_token=body["refresh_token"],
            expires_at=self._clock() + int(body.get("expires_in", 3600)),
        )

    def fresh(self, session: FirebaseSession) -> FirebaseSession:
        """The same session, or a refreshed one if its ID token is about to expire."""
        if session.expires_at - self._clock() > REFRESH_MARGIN:
            return session
        return self.refresh(session)

    def signed_in_recently(self, session: FirebaseSession, within: int = RECENT_SIGN_IN) -> bool:
        """Firebase deletes a login only if its sign-in (auth_time) is recent."""
        try:
            payload = session.id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return self._clock() - int(claims["auth_time"]) < within
        except (IndexError, ValueError, KeyError, TypeError):
            return False

    def send_password_reset(self, email: str) -> None:
        self._post(
            f"{IDENTITY}:sendOobCode",
            json={"requestType": "PASSWORD_RESET", "email": email.strip()},
        )

    def delete_account(self, session: FirebaseSession) -> None:
        """Delete the Firebase login itself (after DELETE /me/data removed the data)."""
        self._post(f"{IDENTITY}:delete", json={"idToken": session.id_token})
