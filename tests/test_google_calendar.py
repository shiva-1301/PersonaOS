"""Google OAuth + Calendar against a fake Google server (httpx.MockTransport).

The real GoogleClient/GoogleService code runs; only the network is replaced.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from app.db.models import CalendarProposal, GoogleToken, OAuthState, User
from app.services.google_client import (
    CALENDAR_SCOPE,
    EVENTS_URL,
    REVOKE_URL,
    TOKEN_URL,
    GoogleClient,
)
from app.services.google_service import GoogleService
from app.services.privacy_service import remaining_data
from tests.conftest import auth
from tests.test_agent import call, script

A, B = auth("test-user-a"), auth("test-user-b")
CLIENT_SECRET = "client-secret-DO-NOT-LOG"


class FakeGoogle:
    """In-memory Google: OAuth token endpoint, revocation and the primary calendar."""

    def __init__(self):
        self.refresh_tokens: dict[str, str] = {}  # refresh token -> owner tag
        self.revoked: set[str] = set()
        self.access_ok: set[str] = set()
        self.events: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.down = False
        self.counter = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(503, json={"error": "backendError"})
        url = str(request.url).split("?")[0]
        if url == TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == [CLIENT_SECRET]
            if form["grant_type"] == ["authorization_code"]:
                code = form["code"][0]
                if code == "bad-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.counter += 1
                body = {
                    "access_token": f"ACCESS-{self.counter}",
                    "expires_in": 3600,
                    "scope": CALENDAR_SCOPE,
                }
                self.access_ok.add(body["access_token"])
                if code != "no-refresh":
                    rt = f"REFRESH-SECRET-{self.counter}"
                    self.refresh_tokens[rt] = code
                    body["refresh_token"] = rt
                return httpx.Response(200, json=body)
            rt = form["refresh_token"][0]
            if rt in self.revoked or rt not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.counter += 1
            token = f"ACCESS-{self.counter}"
            self.access_ok.add(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
        if url == REVOKE_URL:
            self.revoked.add(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if url == EVENTS_URL:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in self.access_ok:
                return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
            if request.method == "POST":
                event = json.loads(request.content)
                event["id"] = f"evt-{len(self.events) + 1}"
                event["htmlLink"] = f"https://calendar.google.com/event?eid={event['id']}"
                self.events.append(event)
                return httpx.Response(200, json=event)
            return httpx.Response(200, json={"items": self.events})
        return httpx.Response(404)


@pytest.fixture
def google(db_app):
    fake = FakeGoogle()
    settings = db_app.state.settings.model_copy(
        update={
            "GOOGLE_CLIENT_ID": "client-id-123",
            "GOOGLE_CLIENT_SECRET": SecretStr(CLIENT_SECRET),
            "TOKEN_ENCRYPTION_KEY": SecretStr(Fernet.generate_key().decode()),
        }
    )
    client = GoogleClient(
        "client-id-123",
        CLIENT_SECRET,
        settings.GOOGLE_REDIRECT_URI,
        http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    db_app.state.services.google = GoogleService(settings, client=client)
    return fake


def state_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def connect(client, headers, code="good-code") -> httpx.Response:
    url = client.get("/integrations/google/start", headers=headers).json()["authorization_url"]
    return client.get(
        "/integrations/google/callback", params={"state": state_from(url), "code": code}
    )


def uid(db_session, auth_uid) -> uuid.UUID:
    return db_session.scalar(select(User.id).where(User.auth_uid == auth_uid))


# --------------------------------------------------------------------------- OAuth


def test_start_builds_offline_consent_url_bound_to_the_user(google, db_client, db_session):
    url = db_client.get("/integrations/google/start", headers=A).json()["authorization_url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == ["client-id-123"]
    assert q["redirect_uri"] == ["http://localhost:8000/integrations/google/callback"]
    assert q["scope"] == [CALENDAR_SCOPE]
    assert (q["access_type"], q["prompt"], q["response_type"]) == (
        ["offline"],
        ["consent"],
        ["code"],
    )
    states = db_session.scalars(select(OAuthState)).all()
    assert [s.user_id for s in states] == [uid(db_session, "test-user-a")]
    assert CLIENT_SECRET not in url
    assert db_client.get("/integrations/google/start").status_code == 401


def test_callback_stores_only_an_encrypted_refresh_token(google, db_app, db_client, db_session):
    resp = connect(db_client, A)
    assert resp.status_code == 200 and "connected" in resp.text
    row = db_session.scalar(select(GoogleToken))
    assert row.user_id == uid(db_session, "test-user-a")
    assert "REFRESH-SECRET" not in row.encrypted_refresh_token  # encrypted at rest
    assert db_app.state.services.google.decrypt(row.encrypted_refresh_token) == "REFRESH-SECRET-1"
    assert row.scopes == CALENDAR_SCOPE
    status = db_client.get("/integrations/google/status", headers=A).json()
    assert status == {
        "configured": True,
        "connected": True,
        "needs_reconnect": False,
        "scopes": [CALENDAR_SCOPE],
    }
    assert "REFRESH" not in json.dumps(status)
    assert db_client.get("/integrations/google/status", headers=B).json()["connected"] is False


def test_state_is_single_use(google, db_client, db_session):
    url = db_client.get("/integrations/google/start", headers=A).json()["authorization_url"]
    state = state_from(url)
    assert (
        db_client.get(
            "/integrations/google/callback", params={"state": state, "code": "good-code"}
        ).status_code
        == 200
    )
    replay = db_client.get(
        "/integrations/google/callback", params={"state": state, "code": "good-code"}
    )
    assert replay.status_code == 400 and "already used" in replay.text


@pytest.mark.parametrize("state", ["", "garbage", "gAAAAABtampered"])
def test_invalid_state_rejected(google, db_client, db_session, state):
    resp = db_client.get(
        "/integrations/google/callback", params={"state": state, "code": "good-code"}
    )
    assert resp.status_code == 400
    assert db_session.scalars(select(GoogleToken)).all() == []


def test_expired_state_rejected(google, db_client, db_session):
    url = db_client.get("/integrations/google/start", headers=A).json()["authorization_url"]
    row = db_session.scalar(select(OAuthState))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    resp = db_client.get(
        "/integrations/google/callback", params={"state": state_from(url), "code": "good-code"}
    )
    assert resp.status_code == 400
    assert db_session.scalars(select(GoogleToken)).all() == []


def test_state_payload_must_match_the_stored_users_nonce(google, db_app, db_client, db_session):
    """Even a correctly encrypted state naming user A is refused if the nonce was B's."""
    db_client.get("/integrations/google/start", headers=B)
    b_nonce = db_session.scalar(select(OAuthState)).nonce
    forged = db_app.state.services.google._fernet.encrypt(
        json.dumps({"n": b_nonce, "u": str(uuid.uuid4())}).encode()
    ).decode()
    resp = db_client.get(
        "/integrations/google/callback", params={"state": forged, "code": "good-code"}
    )
    assert resp.status_code == 400
    assert db_session.scalars(select(GoogleToken)).all() == []


def test_user_denied_or_no_refresh_token(google, db_client, db_session):
    url = db_client.get("/integrations/google/start", headers=A).json()["authorization_url"]
    denied = db_client.get(
        "/integrations/google/callback", params={"state": state_from(url), "error": "access_denied"}
    )
    assert denied.status_code == 400
    no_refresh = connect(db_client, A, code="no-refresh")
    assert no_refresh.status_code == 502
    assert db_session.scalars(select(GoogleToken)).all() == []


def test_disconnect_revokes_at_google_and_deletes(google, db_client, db_session):
    connect(db_client, A)
    assert db_client.delete("/integrations/google", headers=A).status_code == 204
    assert google.revoked == {"REFRESH-SECRET-1"}
    revoke = next(r for r in google.requests if str(r.url).startswith(REVOKE_URL))
    assert "REFRESH" not in str(revoke.url)  # token in the POST body, not the URL
    assert db_session.scalars(select(GoogleToken)).all() == []
    assert db_client.delete("/integrations/google", headers=A).status_code == 404


def test_tokens_and_client_secret_never_logged(google, db_app, db_client, caplog):
    caplog.set_level(logging.DEBUG)
    connect(db_client, A)
    db_app.state.services.google._access.clear()  # force a refresh
    db_client.get("/integrations/google/events", headers=A)
    db_client.delete("/integrations/google", headers=A)
    text = caplog.text + "".join(str(r.__dict__) for r in caplog.records)
    for secret in ("REFRESH-SECRET", "ACCESS-", CLIENT_SECRET):
        assert secret not in text, secret


def test_not_configured_is_a_clear_503(db_app, db_client):
    db_app.state.services.google = None
    assert db_client.get("/integrations/google/start", headers=A).status_code == 503
    assert db_client.get("/integrations/google/status", headers=A).json()["configured"] is False


# --------------------------------------------------------------------------- expiry / outages


def test_expired_or_revoked_token_asks_to_reconnect(google, db_app, db_client, db_session):
    connect(db_client, A)
    google.revoked.add("REFRESH-SECRET-1")  # 7-day Testing expiry, or revoked by the user
    db_app.state.services.google._access.clear()
    resp = db_client.get("/integrations/google/events", headers=A)
    assert resp.status_code == 409
    assert "reconnect" in resp.json()["error"]["message"].lower()
    status = db_client.get("/integrations/google/status", headers=A).json()
    assert (status["connected"], status["needs_reconnect"]) == (False, True)

    connect(db_client, A)  # reconnecting clears the flag
    assert db_client.get("/integrations/google/status", headers=A).json()["connected"] is True
    assert db_client.get("/integrations/google/events", headers=A).status_code == 200


def test_rejected_access_token_is_refreshed_once(google, db_app, db_client):
    connect(db_client, A)
    google.access_ok.clear()  # cached access token now rejected (401)
    assert db_client.get("/integrations/google/events", headers=A).status_code == 200


def test_google_outage_is_a_clear_503(google, db_app, db_client):
    connect(db_client, A)
    google.down = True
    db_app.state.services.google._access.clear()
    resp = db_client.get("/integrations/google/events", headers=A)
    assert resp.status_code == 503


# --------------------------------------------------------------------------- agent + confirmation


def tomorrow_at(hour: int) -> str:
    return (datetime.now(UTC) + timedelta(days=1)).strftime(f"%Y-%m-%dT{hour:02d}:00")


def chat(client, headers, message, session_id=None):
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    resp = client.post("/chat", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def propose(db_app, client, headers, title="Study session", start=None, end=None):
    """One chat turn in which the model proposes an event; returns the response."""
    script(
        db_app,
        call(
            "create_calendar_event",
            title=title,
            start=start or tomorrow_at(18),
            end=end or tomorrow_at(19),
        ),
        "Shall I add it?",
    )
    return chat(client, headers, f"Put {title} on my calendar")


def sent_events(google) -> list[dict]:
    return [
        json.loads(r.content)
        for r in google.requests
        if r.method == "POST" and str(r.url).startswith(EVENTS_URL)
    ]


def test_yes_in_the_next_message_confirms_in_code_without_the_model(
    google, db_app, db_client, db_session
):
    connect(db_client, A)
    first = propose(db_app, db_client, A)
    assert google.events == []  # nothing created yet
    pending = first["pending_confirmations"]
    assert len(pending) == 1 and pending[0]["title"] == "Study session"

    # What happened in the manual check: the model ignores the proposal and asks again.
    model = script(db_app, "Sure! What event would you like to add, and when?")
    second = chat(db_client, A, "Yes, add it", session_id=first["session_id"])
    assert model.prompts == []  # decided in code: the model was never asked
    assert second["tools_used"] == ["confirm_calendar_event"]
    assert "Study session" in second["reply"] and "Done" in second["reply"]
    assert len(google.events) == 1 and google.events[0]["summary"] == "Study session"
    row = db_session.scalar(select(CalendarProposal))
    assert row.status == "created" and row.event_id == "evt-1"
    assert str(row.session_id) == first["session_id"]
    history = db_client.get(f"/chat/sessions/{first['session_id']}", headers=A).json()
    assert [m["content"] for m in history["messages"]][-2:] == ["Yes, add it", second["reply"]]

    # Another yes has nothing left to confirm: a normal agent turn, no duplicate event.
    script(db_app, "You're welcome!")
    third = chat(db_client, A, "ok", session_id=first["session_id"])
    assert third["tools_used"] == [] and len(google.events) == 1


@pytest.mark.parametrize(
    ("zone", "day", "offset"),
    [
        ("Asia/Kolkata", "2027-01-15", "+05:30"),
        ("America/New_York", "2027-01-15", "-05:00"),
        ("America/New_York", "2027-07-15", "-04:00"),  # daylight saving time
    ],
)
def test_events_are_sent_to_google_in_the_users_timezone(
    google, db_app, db_client, db_session, zone, day, offset
):
    connect(db_client, A)
    assert db_client.patch("/me", json={"timezone": zone}, headers=A).status_code == 200
    first = propose(db_app, db_client, A, start=f"{day}T18:00", end=f"{day}T18:30")
    chat(db_client, A, "Yes, add it", session_id=first["session_id"])

    [sent] = sent_events(google)
    assert sent["start"] == {"dateTime": f"{day}T18:00:00{offset}", "timeZone": zone}
    assert sent["end"] == {"dateTime": f"{day}T18:30:00{offset}", "timeZone": zone}
    row = db_session.scalar(select(CalendarProposal))
    assert row.start_at == datetime.fromisoformat(f"{day}T18:00:00{offset}")
    assert row.timezone == zone


def test_yes_only_answers_the_previous_turn_of_the_same_chat(google, db_app, db_client):
    connect(db_client, A)
    first = propose(db_app, db_client, A)
    proposal_id = first["pending_confirmations"][0]["proposal_id"]

    # A yes in ANOTHER chat confirms nothing, and the model can't confirm it either.
    model = script(db_app, call("confirm_calendar_event", proposal_id=proposal_id), "ok")
    other = chat(db_client, A, "yes")
    assert google.events == [] and other["session_id"] != first["session_id"]
    assert "isn't awaiting an answer" in json.loads(model.prompts[1][-1].content)["error"]

    # After an unrelated message in the same chat, a later yes is no longer an answer.
    script(db_app, "It's sunny.")
    chat(db_client, A, "what's the weather like?", session_id=first["session_id"])
    model = script(db_app, call("confirm_calendar_event", proposal_id=proposal_id), "ok")
    chat(db_client, A, "yes", session_id=first["session_id"])
    assert google.events == []
    assert "isn't awaiting an answer" in json.loads(model.prompts[1][-1].content)["error"]
    pending = db_client.get("/integrations/google/proposals", headers=A).json()
    assert [p["id"] for p in pending] == [proposal_id]  # still confirmable by the button


def test_longer_yes_confirms_in_code_then_the_agent_handles_the_rest(google, db_app, db_client):
    connect(db_client, A)
    first = propose(db_app, db_client, A)
    model = script(db_app, call("add_task", title="Buy milk"), "Added the event and the task.")
    reply = chat(
        db_client,
        A,
        "Yes, add it, and also add a task to buy milk for tomorrow please",
        session_id=first["session_id"],
    )
    assert len(google.events) == 1
    assert reply["tools_used"] == ["confirm_calendar_event", "add_task"]
    system = model.prompts[0][0].content
    assert "already confirmed" in system and "created in Google Calendar" in system


def test_yes_when_google_access_expired_says_reconnect(google, db_app, db_client):
    connect(db_client, A)
    first = propose(db_app, db_client, A)
    google.revoked.add("REFRESH-SECRET-1")
    db_app.state.services.google._access.clear()
    reply = chat(db_client, A, "yes please", session_id=first["session_id"])
    assert google.events == []
    assert "couldn't add" in reply["reply"] and "Reconnect Google Calendar" in reply["reply"]
    pending = db_client.get("/integrations/google/proposals", headers=A).json()
    assert len(pending) == 1  # kept: it can be confirmed after reconnecting


def test_streamed_yes_confirms_in_code(google, db_app, db_client):
    connect(db_client, A)
    first = propose(db_app, db_client, A)
    model = script(db_app, "What would you like to add?")
    with db_client.stream(
        "POST",
        "/chat/stream",
        json={"message": "Yes, add it", "session_id": first["session_id"]},
        headers=A,
    ) as resp:
        body = "".join(resp.iter_text())
    assert resp.status_code == 200
    assert "event: tool" in body and "confirm_calendar_event" in body
    assert "event: done" in body and "Study session" in body
    assert model.prompts == [] and len(google.events) == 1


def test_model_cannot_propose_and_confirm_in_the_same_turn(google, db_app, db_client):
    connect(db_client, A)

    def confirm_latest(msgs):
        proposal = json.loads(next(m for m in reversed(msgs) if m.type == "tool").content)
        return call("confirm_calendar_event", proposal_id=proposal["proposal_id"])

    model = script(
        db_app,
        call("create_calendar_event", title="Sneaky", start=tomorrow_at(18), end=tomorrow_at(19)),
        confirm_latest,
        "done",
    )
    chat(db_client, A, "Yes yes, add a study session tomorrow at 6pm")
    assert google.events == []
    refusal = json.loads(model.prompts[2][-1].content)["error"]
    assert "Ask the user to confirm first" in refusal


@pytest.mark.parametrize("reply", ["no", "make it 7pm instead", "wait", "what time was that?"])
def test_anything_but_a_clear_yes_does_not_create(google, db_app, db_client, reply):
    connect(db_client, A)
    script(
        db_app,
        call("create_calendar_event", title="Study", start=tomorrow_at(18), end=tomorrow_at(19)),
        "Confirm?",
    )
    first = chat(db_client, A, "add study tomorrow 6pm")
    proposal_id = first["pending_confirmations"][0]["proposal_id"]
    script(db_app, call("confirm_calendar_event", proposal_id=proposal_id), "ok")
    chat(db_client, A, reply, session_id=first["session_id"])
    assert google.events == []


def test_confirm_and_cancel_via_api_buttons(google, db_app, db_client):
    connect(db_client, A)
    service = db_app.state.services.google
    script(
        db_app,
        call("create_calendar_event", title="One", start=tomorrow_at(8), end=tomorrow_at(9)),
        "?",
    )
    p1 = chat(db_client, A, "add one")["pending_confirmations"][0]["proposal_id"]
    script(
        db_app,
        call("create_calendar_event", title="Two", start=tomorrow_at(10), end=tomorrow_at(11)),
        "?",
    )
    p2 = chat(db_client, A, "add two")["pending_confirmations"][0]["proposal_id"]
    assert len(db_client.get("/integrations/google/proposals", headers=A).json()) == 2

    assert (
        db_client.post(f"/integrations/google/proposals/{p1}/confirm", headers=B).status_code == 404
    )
    ok = db_client.post(f"/integrations/google/proposals/{p1}/confirm", headers=A)
    assert ok.status_code == 200 and ok.json()["status"] == "created" and ok.json()["event_link"]
    assert (
        db_client.post(f"/integrations/google/proposals/{p1}/confirm", headers=A).status_code == 409
    )
    assert (
        db_client.post(f"/integrations/google/proposals/{p2}/cancel", headers=A).json()["status"]
        == "cancelled"
    )
    assert (
        db_client.post(f"/integrations/google/proposals/{p2}/confirm", headers=A).status_code == 409
    )
    assert len(google.events) == 1 and service is db_app.state.services.google


def test_calendar_tools_act_only_for_the_authenticated_user(google, db_app, db_client):
    connect(db_client, A)
    connect(db_client, B)
    script(
        db_app,
        call("create_calendar_event", title="A's", start=tomorrow_at(8), end=tomorrow_at(9)),
        "?",
    )
    a_proposal = chat(db_client, A, "add")["pending_confirmations"][0]["proposal_id"]
    a_id = db_client.get("/me", headers=A).json()["id"]
    model = script(
        db_app, call("confirm_calendar_event", proposal_id=a_proposal, user_id=a_id), "?"
    )
    chat(db_client, B, "yes")  # B's model tries A's proposal and smuggles A's id
    assert "not found" in json.loads(model.prompts[1][-1].content)["error"].lower()
    assert google.events == []


def test_calendar_tool_when_not_connected_or_expired(google, db_app, db_client):
    model = script(
        db_app,
        call("create_calendar_event", title="x", start=tomorrow_at(8), end=tomorrow_at(9)),
        "?",
    )
    chat(db_client, A, "add")
    assert "not connected" in json.loads(model.prompts[1][-1].content)["error"].lower()

    connect(db_client, A)
    google.revoked.add("REFRESH-SECRET-1")
    db_app.state.services.google._access.clear()
    model = script(db_app, call("list_calendar_events"), "?")
    chat(db_client, A, "what's on my calendar?")
    assert "reconnect" in json.loads(model.prompts[1][-1].content)["error"].lower()


def test_no_calendar_tool_can_delete_or_edit():
    from app.agent.tools import ALL_TOOLS

    calendar = {t.name for t in ALL_TOOLS if "calendar" in t.name}
    assert calendar == {"list_calendar_events", "create_calendar_event", "confirm_calendar_event"}


# --------------------------------------------------------------------------- erase all


def test_delete_my_data_revokes_and_removes_google_data(google, db_app, db_client, db_session):
    connect(db_client, A)
    connect(db_client, B)
    script(
        db_app,
        call("create_calendar_event", title="x", start=tomorrow_at(8), end=tomorrow_at(9)),
        "?",
    )
    chat(db_client, A, "add")
    db_client.get("/integrations/google/start", headers=A)  # an unused oauth state too
    services = db_app.state.services
    a, b = uid(db_session, "test-user-a"), uid(db_session, "test-user-b")
    before = remaining_data(db_session, services, a, [])
    assert before["postgres.google_tokens"] == 1 and before["postgres.calendar_proposals"] == 1
    assert before["postgres.oauth_states"] == 1
    b_before = remaining_data(db_session, services, b, [])

    resp = db_client.request("DELETE", "/me/data", json={"confirm": "DELETE MY DATA"}, headers=A)
    assert resp.status_code == 200
    assert "REFRESH-SECRET-1" in google.revoked  # revoked at Google, not only deleted
    assert "REFRESH-SECRET-2" not in google.revoked  # B's untouched
    db_session.expire_all()
    after = remaining_data(db_session, services, a, [])
    assert after == dict.fromkeys(after, 0), after
    assert remaining_data(db_session, services, b, []) == b_before
