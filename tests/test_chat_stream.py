"""POST /chat/stream: Server-Sent Events for agent turns."""

import json
import uuid

from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import ChatMessage, Goal, User
from app.routers.chat import stream_turn
from app.services import background
from tests.conftest import auth
from tests.test_agent import call, script

A, B = auth("test-user-a"), auth("test-user-b")


def stream(client, headers, message, session_id=None) -> tuple[int, list[tuple[str, dict]]]:
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    events = []
    with client.stream("POST", "/chat/stream", json=body, headers=headers) as resp:
        if resp.status_code != 200:
            resp.read()
            return resp.status_code, [("http", resp.json())]
        assert resp.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((event, json.loads(line[len("data: ") :])))
    return 200, events


def kinds(events) -> list[str]:
    return [e for e, _ in events]


def test_plain_answer_streams_tokens_then_done(db_app, db_client):
    script(db_app, "Hello there, how can I help?")
    status, events = stream(db_client, A, "hi")
    assert status == 200
    assert kinds(events) == ["token", "done"]
    assert events[0][1] == {"text": "Hello there, how can I help?"}
    done = events[-1][1]
    assert done["reply"] == "Hello there, how can I help?"
    assert done["memory_status"] == "pending" and done["tools_used"] == []


def test_tool_turn_streams_tool_events_and_saves_everything(db_app, db_client, db_session):
    script(
        db_app, call("create_goal", title="Finish ML course", target_date="2026-11-30"), "Created!"
    )
    _, events = stream(db_client, A, "Add a goal to finish my ML course by 30 Nov")
    assert kinds(events) == ["tool", "token", "done"]
    assert events[0][1] == {"tool": "create_goal", "ok": True}
    done = events[-1][1]
    assert done["tools_used"] == ["create_goal"]
    assert db_session.scalar(select(Goal)).title == "Finish ML course"
    # Messages committed, and memory extraction ran after the stream ended (on the
    # turn's own thread).
    background.wait_for_all(db_app)
    detail = db_client.get(f"/chat/sessions/{done['session_id']}", headers=A).json()
    assert [(m["role"], m["memory_status"]) for m in detail["messages"]] == [
        ("user", "done"),
        ("assistant", None),
    ]


def test_text_before_a_tool_call_is_reset(db_app, db_client):
    thinking = AIMessage(
        content="Let me check your goals.",
        tool_calls=[{"name": "list_goals", "args": {}, "id": "c1", "type": "tool_call"}],
    )
    script(db_app, thinking, "You have no goals yet.")
    _, events = stream(db_client, A, "what are my goals?")
    # Whole-message (non-streaming) models: text riding on a tool call is not streamed.
    assert kinds(events) == ["tool", "token", "done"]
    assert events[-1][1]["reply"] == "You have no goals yet."


def test_reset_emitted_when_streamed_text_is_followed_by_tools(db_app, db_client, monkeypatch):
    """Simulate a streaming model that writes text, then decides to call a tool."""
    real_stream = db_app.state.services.agent_graph.stream

    def fake_stream(graph_input, config, stream_mode):
        yield "messages", (AIMessage(content="Thinking..."), {"langgraph_node": "agent"})
        yield from real_stream(graph_input, config=config, stream_mode=stream_mode)

    graph = db_app.state.services.agent_graph
    monkeypatch.setattr(graph, "stream", fake_stream)
    script(db_app, call("list_goals"), "Done.")
    _, events = stream(db_client, A, "goals?")
    assert kinds(events)[:3] == ["token", "reset", "tool"]
    assert kinds(events)[-2:] == ["token", "done"]


def test_failure_mid_stream_is_an_error_event_and_saves_nothing(db_app, db_client, db_session):
    def boom(msgs):
        raise RuntimeError("secret internals")

    script(db_app, boom)
    status, events = stream(db_client, A, "hi")
    assert status == 200
    assert events == [
        (
            "error",
            {
                "error": {
                    "code": "upstream_error",
                    "message": "The assistant is temporarily unavailable",
                }
            },
        )
    ]
    assert db_session.scalars(select(ChatMessage)).all() == []


def test_checks_happen_before_streaming(db_app, db_client):
    assert db_client.post("/chat/stream", json={"message": "hi"}).status_code == 401
    too_long = "x" * (db_app.state.services.settings.MAX_MESSAGE_CHARS + 1)
    assert stream(db_client, A, too_long)[0] == 422
    assert stream(db_client, A, "hi", session_id=str(uuid.uuid4()))[0] == 404

    script(db_app, "hello")
    a_session = stream(db_client, A, "hi")[1][-1][1]["session_id"]
    status, events = stream(db_client, B, "hi", session_id=a_session)
    assert status == 404  # B cannot stream into A's session


def test_a_turn_finishes_and_is_saved_when_the_client_disconnects(db_app, db_client, db_session):
    """A Streamlit tab that reruns mid-reply closes the stream; the reply must not be lost."""
    user_id = uuid.UUID(db_client.get("/me", headers=A).json()["id"])
    script(
        db_app, call("create_goal", title="English exam", target_date="2026-10-12"), "Created it."
    )
    relay = stream_turn(db_app, user_id, "Add a goal for my English exam on 12 Oct", None)
    first = next(relay)  # the client reads one event ...
    assert first.startswith("event: ")
    relay.close()  # ... and goes away
    background.wait_for_all(db_app)

    assert db_session.scalar(select(Goal).where(Goal.user_id == user_id)).title == "English exam"
    messages = db_session.scalars(
        select(ChatMessage).where(ChatMessage.user_id == user_id).order_by(ChatMessage.created_at)
    ).all()
    assert [(m.role, m.content) for m in messages] == [
        ("user", "Add a goal for my English exam on 12 Oct"),
        ("assistant", "Created it."),
    ]
    assert messages[0].memory_status == "done"
    assert db_session.get(User, user_id) is not None
