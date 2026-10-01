"""Memory lifecycle (fake clock), supersession, privacy endpoints and full erasure."""

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.db.models import MemoryMeta, User
from app.jobs.memory_lifecycle import run_memory_lifecycle
from app.services.memory_lifecycle import LifecycleRules, decayed_strength, next_state, reinforced
from app.services.privacy_service import remaining_data
from tests.conftest import auth
from tests.doc_factory import make_pdf
from tests.test_documents_api import TREES, ready

A, B = auth("test-user-a"), auth("test-user-b")
RULES = LifecycleRules()
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- pure rules


def test_decay_formula():
    assert decayed_strength(T0, 0, T0, 14) == 1.0
    assert math.isclose(decayed_strength(T0, 0, T0 + timedelta(days=14), 14), math.exp(-1))
    # Each access stretches the half-life: S = 14 * (1 + 0.5 * 2) = 28
    assert math.isclose(decayed_strength(T0, 2, T0 + timedelta(days=14), 14), math.exp(-0.5))
    assert decayed_strength(T0, 0, T0 - timedelta(days=1), 14) == 1.0  # clock skew: no boost


@pytest.mark.parametrize(
    "state,strength,expected",
    [
        ("active", 0.9, "active"),
        ("active", 0.49, "stale"),
        ("stale", 0.14, "archived"),
        ("stale", 0.6, "active"),  # reinforced back above the threshold
        ("archived", 0.9, "archived"),  # decay never revives archived memories
        ("superseded", 1.0, "superseded"),  # final
    ],
)
def test_state_transitions(state, strength, expected):
    assert next_state(state, strength, RULES) == expected


def test_reinforcement_is_capped():
    assert reinforced(0.95, RULES) == 1.0
    assert math.isclose(reinforced(0.5, RULES), 0.6)


# --------------------------------------------------------------------------- helpers


def remember(client, headers, text):
    body = client.post("/chat", json={"message": text}, headers=headers).json()
    detail = client.get(f"/chat/sessions/{body['session_id']}", headers=headers).json()
    assert detail["messages"][0]["memory_status"] == "done"
    return body


def user_id(db_session, uid) -> uuid.UUID:
    return db_session.scalar(select(User.id).where(User.auth_uid == uid))


def meta_for(db_session, uid, text) -> MemoryMeta:
    """memory_meta row of the memory whose text equals `text` (fake extractor: verbatim)."""
    db_session.expire_all()
    rows = db_session.scalars(select(MemoryMeta).where(MemoryMeta.user_id == uid)).all()
    return next(r for r in rows if _text(r) == text)


_TEXTS: dict[str, str] = {}


def _text(row) -> str:
    return _TEXTS.get(row.mem0_id, "")


@pytest.fixture(autouse=True)
def _index_texts(db_app, monkeypatch):
    """Remember each memory id's text as it is created (for readable assertions)."""
    _TEXTS.clear()
    svc = db_app.state.services.memory
    real_add = svc._memory.add

    def add(messages, **kw):
        result = real_add(messages, **kw)
        for item in result.get("results", []):
            _TEXTS[item["id"]] = item.get("memory", "")
        return result

    monkeypatch.setattr(svc._memory, "add", add)


# --------------------------------------------------------------------------- decay job


def test_untouched_for_60_days_is_archived_and_not_recalled(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    a = user_id(db_session, "test-user-a")
    memory = db_app.state.services.memory
    now = datetime.now(UTC)
    assert [m.text for m in memory.recall(db_session, a, "study time", now=now)] == [
        "I study best after 6 pm"
    ]

    # 60 days later (from the last access the recall above just recorded).
    stats = run_memory_lifecycle(db_session, memory, now=now + timedelta(days=60))
    row = meta_for(db_session, a, "I study best after 6 pm")
    assert row.state == "archived" and row.strength < 0.15
    assert stats["became_archived"] == 1
    assert memory.recall(db_session, a, "study time") == []
    # Still visible (and deletable) in the memory list, with its state.
    listed = db_client.get("/memory", headers=A).json()
    assert [(m["text"], m["state"]) for m in listed] == [("I study best after 6 pm", "archived")]


def test_stale_memory_is_still_recalled_and_reactivated(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    a = user_id(db_session, "test-user-a")
    memory = db_app.state.services.memory
    later = datetime.now(UTC) + timedelta(days=10)  # e^(-10/14) = 0.49 -> stale
    run_memory_lifecycle(db_session, memory, now=later)
    row = meta_for(db_session, a, "I study best after 6 pm")
    assert row.state == "stale"

    recalled = memory.recall(db_session, a, "study time", now=later)
    assert [m.text for m in recalled] == ["I study best after 6 pm"]
    row = meta_for(db_session, a, "I study best after 6 pm")
    assert (row.state, row.access_count) == ("active", 1)
    assert math.isclose(row.strength, 0.49 + 0.1, abs_tol=0.01)


def test_frequent_access_slows_decay(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "My dog is called Max")
    a = user_id(db_session, "test-user-a")
    now = datetime.now(UTC)
    base = meta_for(db_session, a, "I study best after 6 pm").access_count
    for _ in range(4):  # 4 more recalls of the study fact only
        db_app.state.services.memory.recall(db_session, a, "study best", k=1, now=now)
    run_memory_lifecycle(db_session, db_app.state.services.memory, now=now + timedelta(days=21))
    often = meta_for(db_session, a, "I study best after 6 pm")
    never = meta_for(db_session, a, "My dog is called Max")
    assert often.access_count == base + 4
    expected = math.exp(-21 / (14 * (1 + 0.5 * often.access_count)))
    assert often.state == "active" and math.isclose(often.strength, expected, rel_tol=1e-3)
    assert never.strength < often.strength and never.state in ("stale", "archived")


def test_only_relevant_memories_are_reinforced(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    a = user_id(db_session, "test-user-a")
    memory = db_app.state.services.memory
    memory._settings = memory._settings.model_copy(update={"MEMORY_REINFORCE_MIN_RELEVANCE": 0.99})
    memory.recall(db_session, a, "study time")
    assert meta_for(db_session, a, "I study best after 6 pm").access_count == 0


def test_lifecycle_job_is_idempotent(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "My dog is called Max")
    memory = db_app.state.services.memory
    later = datetime.now(UTC) + timedelta(days=12)
    first = run_memory_lifecycle(db_session, memory, now=later)
    snapshot = {r.mem0_id: (r.state, r.strength) for r in db_session.scalars(select(MemoryMeta))}
    second = run_memory_lifecycle(db_session, memory, now=later)
    again = {r.mem0_id: (r.state, r.strength) for r in db_session.scalars(select(MemoryMeta))}
    assert first["became_stale"] + first["became_archived"] >= 1  # something decayed
    assert {k: v for k, v in second.items() if k.startswith("became_")} == {
        "became_stale": 0,
        "became_archived": 0,
        "became_active": 0,
    }
    assert snapshot == again  # same `now`, same result


def test_job_repairs_drift_both_ways(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    a = user_id(db_session, "test-user-a")
    row = meta_for(db_session, a, "I study best after 6 pm")
    real_id = row.mem0_id
    db_session.delete(row)  # vector without meta
    db_session.add(MemoryMeta(mem0_id=str(uuid.uuid4()), user_id=a))  # meta without vector
    db_session.commit()

    stats = run_memory_lifecycle(db_session, db_app.state.services.memory)
    assert (stats["orphan_meta_removed"], stats["missing_meta_backfilled"]) == (1, 1)
    ids = set(db_session.scalars(select(MemoryMeta.mem0_id).where(MemoryMeta.user_id == a)))
    assert ids == {real_id}


# --------------------------------------------------------------------------- supersession


def test_contradicting_fact_supersedes_the_old_one(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "Actually I now study best in the mornings")
    a = user_id(db_session, "test-user-a")
    old = meta_for(db_session, a, "I study best after 6 pm")
    new = meta_for(db_session, a, "Actually I now study best in the mornings")
    assert (old.state, old.superseded_by) == ("superseded", new.mem0_id)
    assert new.state == "active"

    recalled = db_app.state.services.memory.recall(db_session, a, "when do I study best")
    assert [m.text for m in recalled] == ["Actually I now study best in the mornings"]
    listed = {m["text"]: m for m in db_client.get("/memory", headers=A).json()}
    assert listed["I study best after 6 pm"]["state"] == "superseded"
    assert listed["I study best after 6 pm"]["superseded_by"] == new.mem0_id


def test_unrelated_fact_supersedes_nothing(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "My dog is called Max")
    a = user_id(db_session, "test-user-a")
    assert meta_for(db_session, a, "I study best after 6 pm").state == "active"


def test_supersession_never_touches_another_user(db_app, db_client, db_session):
    remember(db_client, B, "I study best after 6 pm")
    remember(db_client, A, "Actually I now study best in the mornings")
    b = user_id(db_session, "test-user-b")
    assert meta_for(db_session, b, "I study best after 6 pm").state == "active"


def test_failed_supersession_check_keeps_the_new_memory(db_app, db_client, db_session):
    class Broken:
        def invoke(self, *a, **k):
            raise RuntimeError("checker down")

    db_app.state.services.memory._checker = Broken()
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "Actually I now study best in the mornings")
    a = user_id(db_session, "test-user-a")
    assert meta_for(db_session, a, "Actually I now study best in the mornings").state == "active"


# --------------------------------------------------------------------------- cron endpoint


PATH = "/internal/jobs/memory-lifecycle"


def test_cron_endpoint_requires_configured_secret(db_app, db_client):
    assert db_client.post(PATH).status_code == 503  # CRON_SECRET not set
    db_app.state.settings = db_app.state.settings.model_copy(
        update={"CRON_SECRET": SecretStr("s3cret")}
    )
    assert db_client.post(PATH).status_code == 401
    assert db_client.post(PATH, headers={"X-Cron-Secret": "wrong"}).status_code == 401
    assert db_client.post(PATH, headers=A).status_code == 401  # a user token is not enough
    ok = db_client.post(PATH, headers={"X-Cron-Secret": "s3cret"})
    assert ok.status_code == 200 and "checked" in ok.json()
    assert PATH not in db_client.get("/openapi.json").text


# --------------------------------------------------------------------------- memory API


def test_memory_list_and_health_are_private(db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, A, "My dog is called Max")
    listed = db_client.get("/memory", headers=A).json()
    assert {m["text"] for m in listed} == {"I study best after 6 pm", "My dog is called Max"}
    assert all(m["state"] == "active" and m["strength"] == 1.0 for m in listed)
    assert db_client.get("/memory", headers=B).json() == []

    health = db_client.get("/memory/health", headers=A).json()
    assert health["total"] == 2 and health["by_state"]["active"] == 2
    assert health["created_last_7_days"] == 2 and len(health["daily"]) == 7
    assert db_client.get("/memory/health", headers=B).json()["total"] == 0


def test_delete_one_memory_everywhere(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    a = user_id(db_session, "test-user-a")
    mem_id = db_client.get("/memory", headers=A).json()[0]["id"]
    memory = db_app.state.services.memory

    assert db_client.delete(f"/memory/{mem_id}", headers=B).status_code == 404
    assert db_client.delete("/memory/not-a-real-id", headers=A).status_code == 404
    assert len(db_client.get("/memory", headers=A).json()) == 1

    assert db_client.delete(f"/memory/{mem_id}", headers=A).status_code == 204
    assert db_client.get("/memory", headers=A).json() == []
    assert memory.vector_store.get(vector_id=mem_id) is None
    assert db_session.scalars(select(MemoryMeta).where(MemoryMeta.user_id == a)).all() == []
    history, _ = memory.history_counts(a, [mem_id])
    assert history == 0  # including the DELETE row Mem0 writes with the old text


# --------------------------------------------------------------------------- erase all


def _fill(client, headers):
    remember(client, headers, "I study best after 6 pm")
    remember(client, headers, "Actually I now study best in the mornings")
    ready(client, headers, "trees.txt", TREES.encode())
    ready(client, headers, "nets.pdf", make_pdf(["Neural networks use backpropagation."] * 3))
    goal = client.post("/goals", json={"title": "ML course"}, headers=headers).json()
    client.post("/tasks", json={"title": "Read", "goal_id": goal["id"]}, headers=headers)


def test_delete_my_data_leaves_nothing_and_spares_others(db_app, db_client, db_session):
    _fill(db_client, A)
    _fill(db_client, B)
    services = db_app.state.services
    a, b = user_id(db_session, "test-user-a"), user_id(db_session, "test-user-b")
    a_memories = [m["id"] for m in db_client.get("/memory", headers=A).json()]
    b_before = remaining_data(
        db_session, services, b, [m["id"] for m in db_client.get("/memory", headers=B).json()]
    )
    before = remaining_data(db_session, services, a, a_memories)
    assert all(
        before[k] > 0
        for k in (
            "postgres.users",
            "postgres.chat_messages",
            "postgres.goals",
            "postgres.tasks",
            "postgres.documents",
            "postgres.memory_meta",
            "mem0.vectors",
            "mem0.sqlite_history",
            "mem0.sqlite_messages",
            "chroma.document_chunks",
        )
    ), before

    bad = db_client.request("DELETE", "/me/data", json={"confirm": "yes"}, headers=A)
    assert bad.status_code == 422
    assert remaining_data(db_session, services, a, a_memories) == before

    resp = db_client.request("DELETE", "/me/data", json={"confirm": "DELETE MY DATA"}, headers=A)
    assert resp.status_code == 200
    assert resp.json()["deleted"]["chroma.document_chunks"] == before["chroma.document_chunks"]

    db_session.expire_all()
    after = remaining_data(db_session, services, a, a_memories)
    assert after == dict.fromkeys(after, 0), after  # every store, every table: zero
    assert (
        remaining_data(
            db_session, services, b, [m["id"] for m in db_client.get("/memory", headers=B).json()]
        )
        == b_before
    )
    assert db_client.get("/documents/search", params={"q": "entropy"}, headers=B).json()

    # Signing in again starts a brand-new, empty account.
    fresh = db_client.get("/me", headers=A).json()
    assert fresh["id"] != str(a)
    assert db_client.get("/memory", headers=A).json() == []
    assert db_client.get("/goals", headers=A).json() == []


def test_supersession_needs_both_stages_to_agree(db_app, db_client, db_session):
    """Stage 1 (list) over-flags with small models; stage 2 (pairwise) must confirm."""
    from app.services.fakes import ScriptedChatModel

    remember(db_client, A, "My ML exam is on 12 December")
    a = user_id(db_session, "test-user-a")
    checker = ScriptedChatModel(
        replies=['{"facts": [{"n": 1, "both_true": false}]}', '{"conflict": false}']
    )
    checker.prompts, checker.tools_per_call = [], []
    db_app.state.services.memory._checker = checker
    remember(db_client, A, "I am taking the Andrew Ng ML course")
    assert len(checker.prompts) == 2  # list judgement, then the pairwise confirmation
    assert "OLDER: My ML exam is on 12 December" in str(checker.prompts[1][1].content)
    assert meta_for(db_session, a, "My ML exam is on 12 December").state == "active"

    checker.replies = ['{"facts": [{"n": 1, "both_true": false}]}', '{"conflict": true}']
    checker.prompts = []
    remember(db_client, A, "My ML exam moved to 5 January")
    assert meta_for(db_session, a, "My ML exam is on 12 December").state == "superseded"


def test_lifecycle_can_be_limited_to_one_user(db_app, db_client, db_session):
    remember(db_client, A, "I study best after 6 pm")
    remember(db_client, B, "I study best after 6 pm")
    a, b = user_id(db_session, "test-user-a"), user_id(db_session, "test-user-b")
    later = datetime.now(UTC) + timedelta(days=60)
    run_memory_lifecycle(db_session, db_app.state.services.memory, now=later, only_user=a)
    assert meta_for(db_session, a, "I study best after 6 pm").state == "archived"
    assert meta_for(db_session, b, "I study best after 6 pm").state == "active"
