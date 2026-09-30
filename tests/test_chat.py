"""Chat API with cross-session memory, using the offline fake LLM + hashing embedder."""

import re
import uuid

import pytest
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import select

from app.db.models import ChatMessage, MemoryMeta, User
from app.services.fakes import FakeChatModel
from tests.conftest import auth

A, B = auth("test-user-a"), auth("test-user-b")
PREFERENCE = "I study best after 6 pm"
QUESTION = "When should I schedule my study time?"


def memories_in(reply: str) -> str:
    return re.search(r"MEMORIES: (\[.*?\])", reply).group(1)


def chat(client, headers, message, session_id=None):
    body = {"message": message}
    if session_id:
        body["session_id"] = session_id
    return client.post("/chat", json=body, headers=headers)


def user_id(db_session, uid: str) -> uuid.UUID:
    return db_session.scalar(select(User.id).where(User.auth_uid == uid))


# --------------------------------------------------------------------------- basics


def test_chat_creates_session_and_persists_messages(db_client):
    resp = chat(db_client, A, "Hello there")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"].startswith("ECHO: Hello there")
    assert body["memories_used"] == 0

    sessions = db_client.get("/chat/sessions", headers=A).json()
    assert [s["id"] for s in sessions] == [body["session_id"]]
    assert sessions[0]["title"] == "Hello there"

    detail = db_client.get(f"/chat/sessions/{body['session_id']}", headers=A).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Hello there"),
        ("assistant", body["reply"]),
    ]


def test_history_is_sent_within_a_session(db_client):
    sid = chat(db_client, A, "first").json()["session_id"]
    reply = chat(db_client, A, "second", sid).json()["reply"]
    assert "HISTORY: 2" in reply  # previous user + assistant messages
    detail = db_client.get(f"/chat/sessions/{sid}", headers=A).json()
    assert [m["content"] for m in detail["messages"] if m["role"] == "user"] == [
        "first",
        "second",
    ]


def test_history_limit_respected(db_app, db_client):
    db_app.state.services.settings = db_app.state.services.settings.model_copy(
        update={"CHAT_HISTORY_LIMIT": 2}
    )
    sid = chat(db_client, A, "one").json()["session_id"]
    chat(db_client, A, "two", sid)
    assert "HISTORY: 2" in chat(db_client, A, "three", sid).json()["reply"]


# --------------------------------------------------------------------------- memory


def test_memory_carries_across_sessions(db_client, db_session):
    first = chat(db_client, A, PREFERENCE).json()
    # save_turn ran as a background task after the response.
    rows = db_session.scalars(select(MemoryMeta)).all()
    assert len(rows) == 1
    assert rows[0].user_id == user_id(db_session, "test-user-a")
    assert (rows[0].state, rows[0].strength, rows[0].source) == ("active", 1.0, "chat")

    second = chat(db_client, A, QUESTION).json()  # new session
    assert second["session_id"] != first["session_id"]
    assert second["memories_used"] >= 1
    assert PREFERENCE in memories_in(second["reply"])


def test_other_user_never_sees_memories(db_app, db_client, db_session):
    chat(db_client, A, PREFERENCE)
    reply_b = chat(db_client, B, QUESTION).json()
    assert reply_b["memories_used"] == 0
    assert memories_in(reply_b["reply"]) == "[]"

    # And directly at the service layer (B's own question is now B's memory; A's fact
    # must never appear, and every recalled id must be registered to B).
    b_id = user_id(db_session, "test-user-b")
    recalled = db_app.state.services.memory.recall(db_session, b_id, PREFERENCE, k=10)
    assert PREFERENCE not in [m.text for m in recalled]
    owners = {
        row.user_id
        for row in db_session.scalars(
            select(MemoryMeta).where(MemoryMeta.mem0_id.in_([m.id for m in recalled]))
        )
    }
    assert owners <= {b_id}


def test_recall_hides_archived_and_superseded(db_app, db_client, db_session):
    chat(db_client, A, PREFERENCE)
    a_id = user_id(db_session, "test-user-a")
    memory = db_app.state.services.memory
    assert len(memory.recall(db_session, a_id, QUESTION)) == 1

    for state in ("archived", "superseded"):
        row = db_session.scalars(select(MemoryMeta)).one()
        row.state = state
        db_session.commit()
        assert memory.recall(db_session, a_id, QUESTION) == []


def test_recall_ranks_by_relevance_times_strength(db_app, db_client, db_session):
    chat(db_client, A, "I study best after 6 pm")
    chat(db_client, A, "My study group meets on Mondays")
    a_id = user_id(db_session, "test-user-a")
    memory = db_app.state.services.memory
    ranked = memory.recall(db_session, a_id, "study", k=5)
    assert len(ranked) == 2
    weakest = db_session.get(MemoryMeta, ranked[0].id)
    weakest.strength = 0.01
    db_session.commit()
    reranked = memory.recall(db_session, a_id, "study", k=5)
    assert reranked[-1].id == ranked[0].id


def test_memory_save_failure_does_not_break_chat(db_app, db_client, db_session, caplog):
    class Broken:
        def save_turn(self, *a, **k):
            raise RuntimeError("boom")

        def recall(self, *a, **k):
            return []

    db_app.state.services.memory = Broken()
    resp = chat(db_client, A, PREFERENCE)
    assert resp.status_code == 200
    assert "Memory save failed" in caplog.text
    assert db_session.scalars(select(MemoryMeta)).all() == []


def test_recall_failure_degrades_to_no_memories(db_app, db_client, caplog):
    class Down:
        def recall(self, *a, **k):
            raise ConnectionError("ollama down")

        def save_turn(self, *a, **k):
            return []

    db_app.state.services.memory = Down()
    resp = chat(db_client, A, QUESTION)
    assert resp.status_code == 200
    assert resp.json()["memories_used"] == 0
    assert "Memory recall failed" in caplog.text


# --------------------------------------------------------------------------- ownership


def test_sessions_are_private(db_client):
    sid = chat(db_client, A, "secret plans").json()["session_id"]
    assert db_client.get(f"/chat/sessions/{sid}", headers=B).status_code == 404
    assert chat(db_client, B, "hijack", sid).status_code == 404
    assert db_client.get("/chat/sessions", headers=B).json() == []
    # B's failed attempt added nothing to A's session.
    detail = db_client.get(f"/chat/sessions/{sid}", headers=A).json()
    assert len(detail["messages"]) == 2


def test_unknown_session_404(db_client):
    resp = chat(db_client, A, "hi", str(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- validation/errors


def test_requires_auth(db_client):
    assert db_client.post("/chat", json={"message": "hi"}).status_code == 401
    assert db_client.get("/chat/sessions").status_code == 401


@pytest.mark.parametrize("message", ["", "   "])
def test_blank_message_rejected(db_client, message):
    resp = chat(db_client, A, message)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_too_long_message_rejected(db_app, db_client):
    limit = db_app.state.services.settings.MAX_MESSAGE_CHARS
    assert chat(db_client, A, "x" * (limit + 1)).status_code == 422


class RaisingModel(FakeChatModel):
    exc: type[Exception] = RuntimeError

    def _generate(self, *a, **k):
        raise self.exc("provider exploded with internal detail")


def test_llm_failure_is_502_without_internals(db_app, db_client, db_session):
    db_app.state.services.chat_model = RaisingModel()
    resp = chat(db_client, A, "hi")
    assert resp.status_code == 502
    assert "internal detail" not in resp.text
    assert resp.json()["error"]["code"] == "upstream_error"
    assert db_session.scalars(select(ChatMessage)).all() == []  # nothing half-saved


def test_llm_rate_limit_is_503_with_retry_after(db_app, db_client):
    db_app.state.services.chat_model = RaisingModel(exc=ModelRateLimitError)
    resp = chat(db_client, A, "hi")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
    assert resp.json()["error"]["code"] == "service_unavailable"
