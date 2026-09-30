"""Regression: a user's memory survives new sessions and a restart; other users never see it.

Traces the whole path: chat -> background extraction (memory_status) -> Mem0/Chroma
persistence -> retrieval scoped by user_id -> injection into the LLM prompt.

"Restart" offline = throw away every service object (app, Mem0, Chroma client, DB engine)
and build new ones on the same Chroma directory and Postgres database. The real container
restart is covered by scripts/verify_memory_docker.py.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import BaseMessage
from sqlalchemy import select, text

from app.agent.prompts import MEMORIES_HEADER
from app.db.models import Base, ChatMessage, MemoryMeta, User
from app.main import create_app
from app.services.fakes import EXTRACTION_MARKER, FakeChatModel
from app.services.vectorstore import close_chroma_client
from tests.conftest import auth

A, B = auth("test-user-a"), auth("test-user-b")
PREFERENCE = "I study best after 6 pm"
QUESTION = "When should I schedule my study time?"
EXTRACTION_TIMEOUT_S = 30.0


class RecordingChatModel(FakeChatModel):
    """Fake model that records every chat prompt (not Mem0 extraction prompts)."""

    prompts: list[list[BaseMessage]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = "\n".join(str(m.content) for m in messages if m.type == "system")
        if EXTRACTION_MARKER not in system:
            self.prompts.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)

    def last_system_prompt(self) -> str:
        return "\n".join(str(m.content) for m in self.prompts[-1] if m.type == "system")


def injected_memories(system_prompt: str) -> list[str]:
    block = system_prompt.split(MEMORIES_HEADER, 1)[1].split("\n## ", 1)[0]
    return [ln[2:] for ln in block.splitlines() if ln.startswith("- ")]


def wait_for_memory(client, headers, session_id, message_id, timeout=EXTRACTION_TIMEOUT_S):
    """Poll the session until this turn's extraction finishes; fail on timeout/failure."""
    deadline = time.monotonic() + timeout
    while True:
        detail = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
        status = next(m["memory_status"] for m in detail["messages"] if m["id"] == message_id)
        if status == "done":
            return
        assert status != "failed", "memory extraction failed"
        assert time.monotonic() < deadline, f"extraction still {status!r} after {timeout}s"
        time.sleep(0.05)


def build_app(settings):
    app = create_app(settings)
    model = RecordingChatModel()
    model.prompts = []
    app.state.services.chat_model = model
    return app, TestClient(app), model


def shutdown(app, settings):
    """Simulate process exit: drop every in-memory handle to the stores."""
    close_chroma_client(settings.CHROMA_PATH)
    app.state.engine.dispose()


@pytest.fixture
def stores(db_settings):
    yield db_settings
    # Teardown: wipe Postgres for the next test (Chroma dir is per-test tmp_path).
    close_chroma_client(db_settings.CHROMA_PATH)
    app = create_app(db_settings)
    with app.state.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(t.name for t in Base.metadata.sorted_tables)}"))
    app.state.engine.dispose()


def test_memory_persists_across_sessions_and_restart_and_stays_private(stores):
    settings = stores

    # 1. User A states a preference; extraction completes (polled, not slept).
    app1, client1, model1 = build_app(settings)
    first = client1.post("/chat", json={"message": PREFERENCE}, headers=A)
    assert first.status_code == 200
    body = first.json()
    assert body["memory_status"] == "pending"
    wait_for_memory(client1, A, body["session_id"], body["message_id"])

    with app1.state.session_factory() as db:
        a_id = db.scalar(select(User.id).where(User.auth_uid == "test-user-a"))
        stored = db.scalars(select(MemoryMeta).where(MemoryMeta.user_id == a_id)).all()
    assert len(stored) == 1
    memory_id = stored[0].mem0_id

    # 2. A new session retrieves it, and it is injected into the LLM prompt.
    second = client1.post("/chat", json={"message": QUESTION}, headers=A).json()
    assert second["session_id"] != body["session_id"]
    assert second["memories_used"] >= 1
    assert PREFERENCE in injected_memories(model1.last_system_prompt())
    with app1.state.session_factory() as db:
        recalled = app1.state.services.memory.recall(db, a_id, QUESTION)
    assert memory_id in [m.id for m in recalled]

    # 3. "Restart": every service object is discarded and rebuilt from disk/DB.
    shutdown(app1, settings)
    app2, client2, model2 = build_app(settings)
    assert app2.state.services.chroma is not app1.state.services.chroma

    # 4. A retrieves the SAME memory again, in yet another new session.
    third = client2.post("/chat", json={"message": QUESTION}, headers=A).json()
    assert third["memories_used"] >= 1
    assert PREFERENCE in injected_memories(model2.last_system_prompt())
    with app2.state.session_factory() as db:
        recalled_after = app2.state.services.memory.recall(db, a_id, QUESTION)
    assert memory_id in [m.id for m in recalled_after]

    # 5. User B asks the same question after the restart and gets none of A's memory.
    other = client2.post("/chat", json={"message": QUESTION}, headers=B).json()
    assert other["memories_used"] == 0
    assert injected_memories(model2.last_system_prompt()) == []
    with app2.state.session_factory() as db:
        b_id = db.scalar(select(User.id).where(User.auth_uid == "test-user-b"))
        b_recall = app2.state.services.memory.recall(db, b_id, PREFERENCE, k=10)
    assert memory_id not in [m.id for m in b_recall]
    assert PREFERENCE not in [m.text for m in b_recall]
    shutdown(app2, settings)


def test_memory_status_lifecycle_and_failure(db_app, db_client):
    body = db_client.post("/chat", json={"message": PREFERENCE}, headers=A).json()
    wait_for_memory(db_client, A, body["session_id"], body["message_id"])
    detail = db_client.get(f"/chat/sessions/{body['session_id']}", headers=A).json()
    statuses = [(m["role"], m["memory_status"]) for m in detail["messages"]]
    assert statuses == [("user", "done"), ("assistant", None)]

    class Broken:
        def recall(self, *a, **k):
            return []

        def save_turn(self, *a, **k):
            raise RuntimeError("extractor down")

    db_app.state.services.memory = Broken()
    body = db_client.post("/chat", json={"message": "hello"}, headers=A).json()
    with pytest.raises(AssertionError, match="extraction failed"):
        wait_for_memory(db_client, A, body["session_id"], body["message_id"])


def test_only_user_words_are_sent_for_extraction(db_app, db_client):
    calls = []

    class Spy:
        def recall(self, *a, **k):
            return []

        def save_turn(self, db, user_id, messages, **k):
            calls.append(messages)
            return []

    db_app.state.services.memory = Spy()
    db_client.post("/chat", json={"message": PREFERENCE}, headers=A)
    assert calls == [[{"role": "user", "content": PREFERENCE}]]


def test_status_update_is_scoped_to_the_owner(db_app, db_client, db_session):
    """The background task can only mark the message of the user it ran for."""
    from app.routers.chat import save_turn_in_background

    body = db_client.post("/chat", json={"message": "hi"}, headers=A).json()
    msg_id = uuid.UUID(body["message_id"])
    db_session.execute(
        ChatMessage.__table__.update()
        .where(ChatMessage.id == msg_id)
        .values(memory_status="pending")
    )
    db_session.commit()
    save_turn_in_background(db_app, uuid.uuid4(), msg_id, [{"role": "user", "content": "x"}])
    db_session.expire_all()
    assert db_session.get(ChatMessage, msg_id).memory_status == "pending"
