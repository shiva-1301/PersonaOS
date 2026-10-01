"""The Streamlit UI, run headless with Streamlit's AppTest against the real API in-process.

Firebase is faked (httpx.MockTransport); the API uses fake auth, so a Firebase session
whose ID token is "test-user-a" acts as that user.
"""

import json
import time
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.services.fakes import FakeChatModel
from frontend import session
from frontend.api_client import ApiClient
from frontend.firebase_auth import FirebaseSession
from tests.conftest import auth
from tests.test_agent import script

APP = str(Path(__file__).resolve().parent.parent / "frontend" / "streamlit_app.py")
A = auth("test-user-a")
TIMEOUT = 60

pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")


def fake_firebase(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if body.get("password") == "wrong":
        return httpx.Response(400, json={"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
    return httpx.Response(
        200,
        json={
            "localId": "uid-a",
            "email": body.get("email", "a@example.com"),
            "idToken": "test-user-a",  # accepted by the API's fake auth
            "refreshToken": "refresh-a",
            "expiresIn": "3600",
        },
    )


@pytest.fixture
def ui(db_client, monkeypatch):
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-key")
    monkeypatch.setenv("API_URL", "http://testserver")
    monkeypatch.setattr(session, "API_HTTP", db_client)
    monkeypatch.setattr(session, "FIREBASE_TRANSPORT", httpx.MockTransport(fake_firebase))
    return db_client


def signed_in(token: str = "test-user-a") -> FirebaseSession:
    return FirebaseSession("uid-a", "a@example.com", token, "refresh-a", time.time() + 3600)


def app(token: str | None = "test-user-a") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    if token:
        at.session_state[session.AUTH] = signed_in(token)
    return at.run()


def view(name: str) -> AppTest:
    """One page on its own, signed in as user A, wrapped like in the real app."""
    at = AppTest.from_string(
        "import streamlit as st\n"
        "from frontend import session\n"
        f"from frontend.views import {name}\n"
        "if session.signed_in() is None:  # like the real app: signed out -> login\n"
        "    st.title('PersonaOS')\n"
        "    st.stop()\n"
        f"session.page({name}.render)()\n",
        default_timeout=TIMEOUT,
    )
    at.session_state[session.AUTH] = signed_in()
    return at.run()


def texts(at: AppTest) -> str:
    parts = []
    for kind in ("title", "markdown", "caption", "info", "success", "warning", "error"):
        parts += [str(e.value) for e in getattr(at, kind)]
    return "\n".join(parts)


def click(at: AppTest, label: str) -> AppTest:
    next(b for b in at.button if b.label == label).click()
    return at.run()


# --------------------------------------------------------------------------- sign-in


def test_signed_out_shows_the_login_page(ui):
    at = app(token=None)
    assert not at.exception
    assert at.title[0].value == "PersonaOS"
    assert [t.label for t in at.tabs] == ["Sign in", "Create account", "Forgot password"]


def test_sign_in_opens_the_chat_page(ui):
    at = app(token=None)
    at.text_input(key="signin-email").input("a@example.com")
    at.text_input(key="signin-password").input("right")
    at = click(at, "Sign in")
    assert not at.exception
    assert session.AUTH in at.session_state
    assert "Chat" in [t.value for t in at.title]
    assert "a@example.com" in texts(at) or any(
        "a@example.com" in c.value for c in at.sidebar.caption
    )


def test_wrong_password_shows_a_friendly_error(ui):
    at = app(token=None)
    at.text_input(key="signin-email").input("a@example.com")
    at.text_input(key="signin-password").input("wrong")
    at = click(at, "Sign in")
    assert [e.value for e in at.error] == ["Wrong email or password."]
    assert session.AUTH not in at.session_state


def test_an_expired_session_goes_back_to_login_with_a_message(ui):
    at = app(token="expired-token")  # the API answers 401
    assert not at.exception
    assert session.AUTH not in at.session_state
    assert at.title[0].value == "PersonaOS"
    assert [w.value for w in at.warning] == [session.SESSION_EXPIRED]


def test_api_down_shows_a_clear_message_not_a_crash(ui, monkeypatch):
    monkeypatch.setattr(session, "API_HTTP", None)
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # nothing listens here
    at = app()
    assert not at.exception
    message = " ".join(e.value for e in at.error)
    assert "Can't reach the PersonaOS API at http://127.0.0.1:9" in message
    assert "up.ps1" in message
    assert "Try again" in [b.label for b in at.button]


def test_new_accounts_take_the_browsers_timezone_once(ui):
    api = ApiClient("http://testserver", "test-user-a", http=ui)
    assert session.sync_timezone(api, "UTC") is None
    assert session.sync_timezone(api, "Asia/Kolkata") == "Asia/Kolkata"
    assert api.me()["timezone"] == "Asia/Kolkata"
    # A timezone the user already chose is never overwritten.
    assert session.sync_timezone(api, "Europe/London") is None
    assert api.me()["timezone"] == "Asia/Kolkata"


# --------------------------------------------------------------------------- pages


def test_chat_streams_a_reply_and_remembers_the_conversation(ui, db_app):
    script(db_app, "Hello! I'm PersonaOS.")
    at = app()
    at.chat_input[0].set_value("Hi there").run()
    assert not at.exception
    assert "Hello! I'm PersonaOS." in texts(at)
    sessions = ui.get("/chat/sessions", headers=A).json()
    assert at.session_state["chat_session_id"] == sessions[0]["id"]


def test_goals_page_creates_a_goal_and_shows_progress(ui):
    at = view("goals")
    next(t for t in at.text_input if t.label == "Goal").input("Finish ML course")
    at = click(at, "Create goal")
    assert not at.exception
    assert 'Goal "Finish ML course" created.' in texts(at)
    goals = ui.get("/goals", headers=A).json()
    assert [g["title"] for g in goals] == ["Finish ML course"]
    assert "Finish ML course" in texts(at)


def test_goals_page_generates_a_plan(ui, db_app):
    db_app.state.services.planner_model = FakeChatModel()
    ui.post("/goals", json={"title": "ML", "target_date": "2027-01-31"}, headers=A)
    at = view("goals")
    at = click(at, "Generate plan")
    assert not at.exception
    assert "study sessions added" in texts(at)
    assert len(ui.get("/tasks", headers=A).json()) >= 3


def test_dashboard_shows_metrics_and_charts(ui):
    goal = ui.post("/goals", json={"title": "ML"}, headers=A).json()
    task = ui.post("/tasks", json={"title": "t", "goal_id": goal["id"]}, headers=A).json()
    ui.patch(f"/tasks/{task['id']}", json={"status": "done"}, headers=A)
    at = view("dashboard")
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Current streak"] == "1 day"
    assert metrics["Completed, last 7 days"] == "1"
    assert len(at.get("plotly_chart")) == 2  # completions + goal progress (no memories yet)
    assert "No memories yet" in texts(at)


def test_documents_memory_and_integrations_pages_render(ui):
    for name, expected in [
        ("documents", "No documents yet"),
        ("memory", "Nothing remembered yet"),
        ("integrations", "isn't set up on this server"),
    ]:
        at = view(name)
        assert not at.exception, name
        assert expected in texts(at), name


def test_delete_everything_from_the_memory_page(ui):
    ui.post("/goals", json={"title": "Something"}, headers=A)
    at = view("memory")
    next(t for t in at.text_input if "DELETE MY DATA" in t.label).input("DELETE MY DATA")
    at = click(at, "Delete all my data")
    assert not at.exception
    assert session.AUTH not in at.session_state  # signed out afterwards
    assert at.session_state[session.NOTICE] == "All your PersonaOS data was deleted."
    assert ui.get("/goals", headers=A).json() == []


def test_delete_everything_needs_the_exact_phrase(ui):
    ui.post("/goals", json={"title": "Something"}, headers=A)
    at = view("memory")
    next(t for t in at.text_input if "DELETE MY DATA" in t.label).input("delete")
    at = click(at, "Delete all my data")
    assert 'Type exactly "DELETE MY DATA".' in texts(at)
    assert len(ui.get("/goals", headers=A).json()) == 1
