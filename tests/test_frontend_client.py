"""frontend/api_client.py: error mapping and SSE parsing (fake transport), and a
contract test against the real API in-process (FastAPI TestClient, fake auth)."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.services.fakes import FakeChatModel
from frontend.api_client import (
    ApiClient,
    ApiError,
    ApiUnavailable,
    PayloadTooLarge,
    Unauthorized,
)
from tests.test_agent import script

# The client always sets timeouts; the in-process TestClient just ignores them.
pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")

# --------------------------------------------------------------------------- fake transport


def client_for(handler, token="tok") -> ApiClient:
    return ApiClient("http://api.test", token, transport=httpx.MockTransport(handler))


def envelope(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


def test_sends_a_fresh_token_with_every_request():
    seen, tokens = [], iter(["first", "second"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"id": "u1", "timezone": "UTC"})

    api = ApiClient("http://api.test", lambda: next(tokens), transport=httpx.MockTransport(handler))
    api.me()
    api.me()
    assert seen == ["Bearer first", "Bearer second"]


def test_health_is_sent_without_a_token():
    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"status": "ok"})

    assert client_for(handler).health() == {"status": "ok"}


def test_none_query_params_are_dropped():
    def handler(request):
        assert dict(request.url.params) == {"due": "overdue"}
        return httpx.Response(200, json=[])

    assert client_for(handler).tasks(due="overdue") == []


@pytest.mark.parametrize(
    ("response", "kind", "message"),
    [
        (envelope(401, "unauthorized", "Invalid token"), Unauthorized, "Invalid token"),
        (envelope(413, "payload_too_large", "Max 10 MB"), PayloadTooLarge, "Max 10 MB"),
        (envelope(409, "conflict", "Reconnect Google"), ApiError, "Reconnect Google"),
        (envelope(503, "service_unavailable", "Busy"), ApiError, "Busy"),
        (httpx.Response(500, text="<html>boom</html>"), ApiError, "had a problem"),
    ],
)
def test_errors_keep_the_apis_message(response, kind, message):
    with pytest.raises(kind) as caught:
        client_for(lambda request: response).goals()
    assert type(caught.value) is kind
    assert message in caught.value.message
    assert caught.value.status == response.status_code


def test_unreachable_or_slow_api_is_reported_as_unavailable():
    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiUnavailable, match="Can't reach the PersonaOS API at http://api.test"):
        client_for(refused).goals()
    with pytest.raises(ApiUnavailable, match="took too long"):
        client_for(slow).goals()


def sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def test_stream_yields_tokens_resets_tools_and_the_final_reply():
    body = sse(
        ("token", {"text": "Let me "}),
        ("reset", {}),
        ("tool", {"tool": "list_tasks", "ok": True}),
        ("token", {"text": "You have "}),
        ("token", {"text": "2 tasks."}),
        ("done", {"reply": "You have 2 tasks.", "session_id": "s1"}),
    )

    def handler(request):
        assert json.loads(request.content) == {"message": "tasks?", "session_id": None}
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = list(client_for(handler).chat_stream("tasks?"))
    assert [e.event for e in events] == ["token", "reset", "tool", "token", "token", "done"]
    assert events[-1].data["reply"] == "You have 2 tasks."


def test_stream_error_event_raises_with_its_message():
    body = sse(("error", {"error": {"code": "service_unavailable", "message": "Busy, try later"}}))
    api = client_for(lambda request: httpx.Response(200, content=body))
    with pytest.raises(ApiError, match="Busy, try later"):
        list(api.chat_stream("hi"))


def test_stream_http_error_is_mapped_like_any_call():
    api = client_for(lambda request: envelope(401, "unauthorized", "Expired"))
    with pytest.raises(Unauthorized):
        list(api.chat_stream("hi"))


# --------------------------------------------------------------------------- real API


@pytest.fixture
def api(db_client) -> ApiClient:
    return ApiClient("http://testserver", "test-user-a", http=db_client)


def test_contract_account_goals_tasks_plan_and_analytics(api, db_app):
    me = api.update_me(timezone="Asia/Kolkata")
    assert me["timezone"] == "Asia/Kolkata" and api.me()["id"] == me["id"]

    target = date.today() + timedelta(days=27)
    goal = api.create_goal("Finish ML course", target_date=target, description="Coursera")
    assert goal["target_date"] == target.isoformat() and goal["progress"]["total"] == 0
    assert api.update_goal(goal["id"], status="paused")["status"] == "paused"
    assert [g["id"] for g in api.goals(status="paused")] == [goal["id"]]

    # A naive due time is read in the profile's timezone: 18:00 Kolkata = 12:30 UTC.
    due = datetime.combine(date.today() + timedelta(days=1), datetime.min.time()).replace(hour=18)
    task = api.create_task("Read chapter 3", due_at=due, goal_id=goal["id"], est_minutes=45)
    stored = datetime.fromisoformat(task["due_at"]).astimezone(UTC)
    assert (stored.hour, stored.minute) == (12, 30)
    done = api.update_task(task["id"], status="done")
    assert done["status"] == "done" and done["completed_at"]

    db_app.state.services.planner_model = FakeChatModel()
    plan = api.generate_plan(goal["id"], 6)
    assert len(plan["tasks"]) >= 3 and plan["goal_id"] == goal["id"]

    summary = api.analytics(days=7, weeks=4)
    assert summary["timezone"] == "Asia/Kolkata" and len(summary["completions_per_day"]) == 7
    assert summary["completed_last_7_days"] == 1 and summary["streak"]["current"] == 1
    assert summary["goals"][0]["done"] == 1

    api.delete_task(task["id"])
    api.delete_goal(goal["id"])
    assert api.goals() == []


def test_contract_documents(api):
    doc = api.upload_document(
        "notes.txt", b"Entropy measures impurity in decision trees.", "text/plain"
    )
    assert doc["filename"] == "notes.txt" and doc["status"] in ("processing", "ready")
    assert [d["id"] for d in api.documents()] == [doc["id"]]
    assert api.document(doc["id"])["status"] == "ready"
    hits = api.search_documents("entropy")
    assert hits and hits[0]["filename"] == "notes.txt"
    api.delete_document(doc["id"])
    assert api.documents() == []


def test_contract_streamed_chat_and_sessions(api, db_app):
    script(db_app, "Hello! How can I help?")
    events = list(api.chat_stream("Hi there"))
    done = events[-1]
    assert done.event == "done" and done.data["reply"] == "Hello! How can I help?"
    assert "".join(e.data["text"] for e in events if e.event == "token") == done.data["reply"]
    sessions = api.chat_sessions()
    assert [s["id"] for s in sessions] == [done.data["session_id"]]
    messages = api.chat_session(done.data["session_id"])["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_contract_memory_google_and_errors(api, db_client):
    assert api.memories() == [] and api.memory_health()["total"] == 0
    assert api.google_status()["configured"] is False
    with pytest.raises(ApiError) as missing:
        api.update_task("00000000-0000-0000-0000-000000000000", status="done")
    assert missing.value.status == 404 and "not found" in missing.value.message.lower()
    with pytest.raises(Unauthorized):
        ApiClient("http://testserver", None, http=db_client).me()
    with pytest.raises(Unauthorized):
        ApiClient("http://testserver", "forged-token", http=db_client).me()


def test_contract_delete_all_data(api):
    api.create_goal("Something")
    result = api.delete_all_data()
    assert result["deleted"]["postgres.goals"] == 1
    assert api.goals() == []  # a fresh, empty account on the next call


def test_contract_upload_over_the_limit_is_payload_too_large(db_settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(db_settings.model_copy(update={"MAX_UPLOAD_MB": 1}))
    try:
        api = ApiClient("http://testserver", "test-user-a", http=TestClient(app))
        with pytest.raises(PayloadTooLarge):
            api.upload_document("big.txt", b"x" * (1024 * 1024 + 10), "text/plain")
    finally:
        app.state.engine.dispose()
