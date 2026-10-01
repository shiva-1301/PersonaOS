"""Streamlit glue: who is signed in, a fresh-token API client, friendly errors.

The Firebase session lives only in this browser tab's st.session_state. Each API
call asks for a token; it is refreshed shortly before it expires, and a refresh
failure or any 401 signs the user out with a message on the login page.
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from frontend.api_client import ApiClient, ApiError, ApiUnavailable, PayloadTooLarge, Unauthorized
from frontend.config import load_settings
from frontend.firebase_auth import AuthError, FirebaseAuth, FirebaseSession

# Test hooks; None = the real network. API_HTTP: a ready httpx.Client for the API
# (tests pass FastAPI's TestClient); the transports: httpx.MockTransport instances.
API_HTTP = None
API_TRANSPORT = None
FIREBASE_TRANSPORT = None

STATIC = Path(__file__).resolve().parent / "static"

AUTH = "auth"
NOTICE = "notice"
FLASH = "flash"
SESSION_EXPIRED = "Your session expired. Please sign in again."


def settings():
    return load_settings()


def firebase() -> FirebaseAuth:
    return FirebaseAuth(settings().firebase_api_key, transport=FIREBASE_TRANSPORT)


def signed_in() -> FirebaseSession | None:
    return st.session_state.get(AUTH)


def start_session(session: FirebaseSession) -> None:
    st.session_state.pop(NOTICE, None)
    st.session_state[AUTH] = session


def sign_out(notice: str | None = None) -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if notice:
        st.session_state[NOTICE] = notice


def _token() -> str:
    session = signed_in()
    if session is None:
        raise Unauthorized(401, "unauthorized", "Please sign in.")
    try:
        fresh = firebase().fresh(session)
    except AuthError as exc:
        raise Unauthorized(401, "unauthorized", exc.message) from None
    if fresh is not session:
        st.session_state[AUTH] = fresh
    return fresh.id_token


def client() -> ApiClient:
    return ApiClient(settings().api_url, _token, transport=API_TRANSPORT, http=API_HTTP)


# --------------------------------------------------------------------------- feedback


def flash(message: str) -> None:
    """A success message shown once, after the next rerun."""
    st.session_state.setdefault(FLASH, []).append(message)


def show_flash() -> None:
    for message in st.session_state.pop(FLASH, []):
        st.success(message)


def attempt(fn, *args, **kwargs):
    """Run one API action; show a friendly error instead of crashing the page.

    Returns the result (True for a success without a body, e.g. a delete), or None
    if it failed. A 401 or an unreachable API still propagates to `page()`, which
    handles them for the whole page."""
    try:
        result = fn(*args, **kwargs)
        return True if result is None else result
    except (Unauthorized, ApiUnavailable):
        raise
    except PayloadTooLarge:
        st.error(f"That file is too large. The limit is {settings().max_upload_mb} MB.")
    except ApiError as exc:
        st.error(exc.message)
    return None


def show_unavailable(exc: ApiUnavailable) -> None:
    st.error(
        f"{exc.message} Is the stack running? Start it with `.\\scripts\\up.ps1` "
        "(and make sure Docker Desktop is open)."
    )
    if st.button("Try again", key="retry-api"):
        st.rerun()


def page(view):
    """Wrap a page so a 401 returns to sign-in and an API outage shows a clear message."""

    def run() -> None:
        try:
            show_flash()
            view()
        except Unauthorized:
            sign_out(SESSION_EXPIRED)
            st.rerun()
        except ApiUnavailable as exc:
            show_unavailable(exc)
        except PayloadTooLarge:
            st.error(f"That file is too large. The limit is {settings().max_upload_mb} MB.")
        except ApiError as exc:
            st.error(exc.message)

    run.__name__ = f"page_{view.__module__.rsplit('.', 1)[-1]}"
    return run


# --------------------------------------------------------------------------- account


def sync_timezone(api: ApiClient, browser: str | None) -> str | None:
    """New accounts start in UTC: adopt the browser's timezone at sign-in.

    Only a profile still on the UTC default is changed; returns the new timezone."""
    if not browser or browser == "UTC":
        return None
    if api.me()["timezone"] != "UTC":
        return None
    try:
        api.update_me(timezone=browser)
    except ApiError:
        return None  # e.g. a browser zone name the API doesn't know
    return browser


def me(api: ApiClient) -> dict:
    if "me" not in st.session_state:
        st.session_state["me"] = api.me()
    return st.session_state["me"]


def user_zone(api: ApiClient) -> ZoneInfo:
    try:
        return ZoneInfo(me(api)["timezone"])
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def local_time(iso: str | None, tz: ZoneInfo, fmt: str = "%a %d %b, %H:%M") -> str:
    """An API timestamp shown in the user's profile timezone (not the server's)."""
    if not iso:
        return ""
    moment = datetime.fromisoformat(str(iso))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).strftime(fmt)
