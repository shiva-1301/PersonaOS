"""Long-term user memory: Mem0 (extraction + vectors in Chroma) plus `memory_meta` in
Postgres for the lifecycle layer.

Lifecycle (Phase 7, rules in memory_lifecycle.py):
- save_turn registers new memories (active, strength 1.0) and runs a contradiction check:
  similar existing memories that the new fact replaces become `superseded`.
- recall drops archived/superseded memories, ranks by relevance x strength, and reinforces
  the relevant ones (access_count+1, last access = now, strength + step, stale -> active).
- Decay happens in the nightly job (app/jobs/memory_lifecycle.py).
- Privacy: list, delete one, and purge everything for a user, including Mem0's SQLite
  history (which otherwise keeps deleted text).

Isolation: every Mem0 call is scoped by `user_id` (the users.id UUID as a string) and
every `memory_meta` query filters on the same user. `_mem0_user_id` is the only place
that converts the id.
"""

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agent.prompts import SUPERSEDE_MARKER
from app.config import Settings
from app.db.models import MemoryMeta
from app.services.llm import embedding_space_id, invoke_with_backoff
from app.services.memory_lifecycle import HIDDEN_STATES, LifecycleRules, next_state, reinforced

logger = logging.getLogger(__name__)

EXTRACTION_INSTRUCTIONS = """\
Only extract durable facts that the USER states about themselves: preferences, habits,
schedule, goals, studies, work, constraints and personal details they volunteer.
Questions and requests are not facts: "When should I study?" reveals nothing durable,
so extract nothing from it. Do NOT store the assistant's suggestions, general knowledge,
greetings, or guesses. Never store passwords, API keys, card numbers or other secrets.
Write each memory as a short third-person statement about the user."""

SUPERSEDE_PROMPT = f"""\
You are the {SUPERSEDE_MARKER}. You keep one user's memory consistent.
You get one NEW fact and numbered EXISTING facts about the same user. For EACH existing
fact decide: could the NEW fact and this EXISTING fact BOTH be true at the same time?
- A user can have several pets, several goals, several preferences about different things,
  a course AND an exam, a time-of-day preference AND a weekly amount of study.
- They can NOT both be true only when they give different values for the very same thing
  (the same exam's date, where the user lives now, the same preference changed).
Return ONLY JSON: {{"facts": [{{"n": <number>, "both_true": true|false}}]}} with one
entry per existing fact."""

CONFLICT_PROMPT = f"""\
You are the {SUPERSEDE_MARKER} (confirmation step). You compare two facts about the same
user. Answer whether they CONFLICT: they give different values for exactly the same thing,
so the newer one replaces the older one (e.g. the same exam on two different dates; living
in two different cities now; a preference that changed). If they are about different
things, or both can be true together, they do NOT conflict.
Return ONLY JSON: {{"conflict": true}} or {{"conflict": false}}."""


@dataclass(frozen=True)
class RecalledMemory:
    id: str
    text: str
    relevance: float
    strength: float

    @property
    def score(self) -> float:
        return self.relevance * self.strength


def _mem0_user_id(user_id: uuid.UUID) -> str:
    return str(user_id)


def mem0_collection_name(settings: Settings) -> str:
    return f"personaos_mem0__{embedding_space_id(settings)}"


def build_memory(settings: Settings, chat_model: BaseChatModel, embedder: Embeddings, chroma):
    """Build a Mem0 `Memory` wired to our models and the shared Chroma client."""
    data_dir = Path(settings.CHROMA_PATH).resolve()
    # Must be set before mem0 is imported: keeps its files on our volume, no telemetry.
    os.environ["MEM0_DIR"] = str(data_dir / ".mem0")
    os.environ["MEM0_TELEMETRY"] = "False"

    from mem0 import Memory
    from mem0.utils.factory import EmbedderFactory, LlmFactory

    from app.services.mem0_adapters import Mem0ChatAdapter, Mem0EmbedderAdapter

    # Mem0 validates provider names against a fixed list; route "langchain" to our adapters.
    LlmFactory.provider_to_class["langchain"] = (
        f"{Mem0ChatAdapter.__module__}.{Mem0ChatAdapter.__name__}",
        LlmFactory.provider_to_class["langchain"][1],
    )
    EmbedderFactory.provider_to_class["langchain"] = (
        f"{Mem0EmbedderAdapter.__module__}.{Mem0EmbedderAdapter.__name__}"
    )

    memory = Memory.from_config(
        {
            "llm": {"provider": "langchain", "config": {"model": chat_model}},
            "embedder": {"provider": "langchain", "config": {"model": embedder}},
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": mem0_collection_name(settings),
                    "client": chroma,
                    "path": str(data_dir),  # required by Mem0's validator; client wins
                },
            },
            "history_db_path": str(data_dir / "mem0_history.db"),
            "custom_instructions": EXTRACTION_INSTRUCTIONS,
        }
    )
    memory.llm.config.rate_limit_attempts = settings.LLM_RATE_LIMIT_ATTEMPTS
    # spaCy is intentionally not installed (entity linking/BM25 off); Mem0 warns every call.
    logging.getLogger("mem0.utils.spacy_models").setLevel(logging.ERROR)
    return memory


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _json_object(text: str) -> str:
    first, last = text.find("{"), text.rfind("}")
    return text[first : last + 1] if first != -1 and last > first else text


class MemoryService:
    def __init__(
        self,
        memory: Any,
        settings: Settings | None = None,
        checker: BaseChatModel | None = None,
        session_factory=None,
    ):
        self._memory = memory
        # Own short transactions for lifecycle bookkeeping (see _bookkeeping).
        self._session_factory = session_factory
        self._settings = settings or Settings(_env_file=None)
        # Model for the contradiction check (the memory model, in JSON mode).
        self._checker = checker
        s = self._settings
        self.rules = LifecycleRules(
            base_days=s.MEMORY_DECAY_BASE_DAYS,
            stale_below=s.MEMORY_STALE_BELOW,
            archive_below=s.MEMORY_ARCHIVE_BELOW,
            reinforce_step=s.MEMORY_REINFORCE_STEP,
        )

    @property
    def vector_store(self):
        """Mem0's vector store (used by the nightly job to find drift)."""
        return self._memory.vector_store

    # ------------------------------------------------------------------ write path

    def save_turn(
        self,
        db: Session,
        user_id: uuid.UUID,
        messages: list[dict[str, str]],
        *,
        source: str = "chat",
        infer: bool = True,
        now: datetime | None = None,
    ) -> list[str]:
        """Extract memories from a conversation turn and register them in memory_meta.

        infer=False stores the message text verbatim (no LLM extraction)."""
        now = now or datetime.now(UTC)
        result = self._memory.add(
            messages, user_id=_mem0_user_id(user_id), metadata={"source": source}, infer=infer
        )
        events = result.get("results", []) if isinstance(result, dict) else []
        saved: list[tuple[str, str]] = []
        for item in events:
            mem_id, event = item.get("id"), item.get("event")
            if not mem_id:
                continue
            if event in ("ADD", "UPDATE"):
                self._upsert_meta(db, mem_id, user_id, source, now)
                saved.append((mem_id, item.get("memory") or ""))
            elif event == "DELETE":
                db.query(MemoryMeta).filter(
                    MemoryMeta.mem0_id == mem_id, MemoryMeta.user_id == user_id
                ).delete()
        db.flush()
        if saved and self._checker is not None and self._settings.MEMORY_SUPERSEDE_ENABLED:
            new_ids = {mem_id for mem_id, _ in saved}
            for mem_id, text in saved:
                try:
                    self._supersede(db, user_id, mem_id, text, exclude=new_ids)
                except Exception as exc:  # never lose the new memory over the check
                    logger.warning("Supersession check failed", extra={"error": type(exc).__name__})
        db.commit()
        return [mem_id for mem_id, _ in saved]

    def remember(self, db: Session, user_id: uuid.UUID, fact: str) -> list[str]:
        """Store a fact the user explicitly asked to be remembered, word for word."""
        return self.save_turn(
            db, user_id, [{"role": "user", "content": fact}], source="manual", infer=False
        )

    def _supersede(
        self, db: Session, user_id: uuid.UUID, new_id: str, new_text: str, exclude: set[str]
    ) -> list[str]:
        """Mark existing memories that `new_text` makes outdated as superseded.

        Mem0 2.2.1 is ADD-only, so this is our own check: similar existing memories
        (vector score >= MEMORY_SUPERSEDE_MIN_SCORE) are judged by the memory model in two
        stages (list judgement, then pairwise confirmation); both must agree."""
        if not new_text.strip():
            return []
        hits = self._memory.search(
            new_text, filters={"user_id": _mem0_user_id(user_id)}, top_k=6
        ).get("results", [])
        floor = self._settings.MEMORY_SUPERSEDE_MIN_SCORE
        ids = [h["id"] for h in hits if h["id"] not in exclude and (h.get("score") or 0) >= floor]
        if not ids:
            return []
        live = {
            m.mem0_id: m
            for m in db.scalars(
                select(MemoryMeta).where(
                    MemoryMeta.user_id == user_id,
                    MemoryMeta.mem0_id.in_(ids),
                    MemoryMeta.state.in_(("active", "stale")),
                )
            )
        }
        candidates = [h for h in hits if h["id"] in live]
        if not candidates:
            return []
        listing = "\n".join(f"{i + 1}. {h.get('memory', '')}" for i, h in enumerate(candidates))
        verdict = self._ask_json(SUPERSEDE_PROMPT, f"NEW: {new_text}\nEXISTING:\n{listing}")
        flagged = [
            candidates[f["n"] - 1]
            for f in (verdict.get("facts") or [])
            if isinstance(f, dict)
            and f.get("both_true") is False
            and isinstance(f.get("n"), int)
            and 1 <= f["n"] <= len(candidates)
        ]
        replaced = []
        for hit in flagged:
            # Stage 2: a focused pairwise question. A small model over-flags in stage 1
            # (e.g. "taking the ML course" vs "exam on 12 Dec"); requiring both stages to
            # agree removed every false positive in calibration (docs/DECISIONS.md).
            confirm = self._ask_json(
                CONFLICT_PROMPT, f"OLDER: {hit.get('memory', '')}\nNEWER: {new_text}"
            )
            if confirm.get("conflict") is True:
                row = live[hit["id"]]
                row.state, row.superseded_by = "superseded", new_id
                replaced.append(row.mem0_id)
        if replaced:
            logger.info("Memories superseded", extra={"count": len(replaced)})
        return replaced

    def _ask_json(self, system: str, content: str) -> dict:
        reply = invoke_with_backoff(
            self._checker,
            [SystemMessage(system), HumanMessage(content)],
            attempts=self._settings.LLM_RATE_LIMIT_ATTEMPTS,
        )
        try:
            data = json.loads(_json_object(str(reply.content)))
        except ValueError:
            logger.info("Unparseable supersession verdict; treated as no change")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _upsert_meta(
        db: Session, mem_id: str, user_id: uuid.UUID, source: str, now: datetime
    ) -> None:
        values = {
            "mem0_id": mem_id,
            "user_id": user_id,
            "state": "active",
            "strength": 1.0,
            "access_count": 0,
            "last_accessed_at": now,
            "source": source,
        }
        stmt = pg_insert(MemoryMeta).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MemoryMeta.mem0_id],
            set_={k: stmt.excluded[k] for k in ("state", "strength", "last_accessed_at")},
            where=MemoryMeta.user_id == user_id,  # never touch another user's row
        )
        db.execute(stmt)

    @staticmethod
    def backfill_meta(db: Session, mem_id: str, user_id: uuid.UUID, now: datetime):
        """A Mem0 memory without a meta row (drift) is registered as active."""
        db.execute(
            pg_insert(MemoryMeta)
            .values(
                mem0_id=mem_id,
                user_id=user_id,
                state="active",
                strength=1.0,
                access_count=0,
                last_accessed_at=now,
                source="chat",
            )
            .on_conflict_do_nothing(index_elements=[MemoryMeta.mem0_id])
        )
        return db.scalar(
            select(MemoryMeta).where(MemoryMeta.mem0_id == mem_id, MemoryMeta.user_id == user_id)
        )

    # ------------------------------------------------------------------ read path

    @contextmanager
    def _bookkeeping(self, db: Session):
        """Session for reinforcement/backfill writes, committed on its own.

        These must not sit uncommitted in the caller's transaction: agent tools run in
        other sessions while the turn is open, and a tool saving a memory (supersession)
        would wait forever on rows the turn had locked."""
        if self._session_factory is None:
            yield db
            db.flush()
            return
        with self._session_factory() as own:
            yield own
            own.commit()

    def recall(
        self,
        db: Session,
        user_id: uuid.UUID,
        query: str,
        k: int = 5,
        *,
        now: datetime | None = None,
    ) -> list[RecalledMemory]:
        """Top-k usable memories for `query`, reinforcing the relevant ones (committed in
        a separate bookkeeping transaction when a session factory is configured)."""
        query = (query or "").strip()
        if not query:
            return []
        now = now or datetime.now(UTC)
        uid = _mem0_user_id(user_id)
        hits = self._memory.search(query, filters={"user_id": uid}, top_k=k * 3)
        hits = [
            h
            for h in (hits.get("results", []) if isinstance(hits, dict) else [])
            # Defence in depth: Mem0 already filtered, but never trust a foreign row.
            if h.get("id") and h.get("user_id") in (None, uid)
        ]
        if not hits:
            return []

        results = []
        with self._bookkeeping(db) as tx:
            meta = {
                m.mem0_id: m
                for m in tx.scalars(
                    select(MemoryMeta).where(
                        MemoryMeta.user_id == user_id,
                        MemoryMeta.mem0_id.in_([h["id"] for h in hits]),
                    )
                )
            }
            candidates: list[tuple[dict, MemoryMeta]] = []
            for h in hits:
                m = meta.get(h["id"]) or self.backfill_meta(tx, h["id"], user_id, now)
                if m is None or m.state in HIDDEN_STATES:
                    continue
                candidates.append((h, m))
            candidates.sort(
                key=lambda pair: float(pair[0].get("score") or 0) * pair[1].strength,
                reverse=True,
            )
            for h, m in candidates[:k]:
                relevance = float(h.get("score") or 0.0)
                results.append(RecalledMemory(h["id"], h.get("memory", ""), relevance, m.strength))
                # Only memories that matter to the question are reinforced; otherwise every
                # recall would refresh everything and nothing would ever fade.
                if relevance >= self._settings.MEMORY_REINFORCE_MIN_RELEVANCE:
                    m.access_count += 1
                    m.last_accessed_at = now
                    m.strength = reinforced(m.strength, self.rules)
                    m.state = next_state(m.state, m.strength, self.rules)  # stale -> active
        return results

    # ------------------------------------------------------------------ privacy

    def list_memories(
        self, db: Session, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> list[dict]:
        """All of the user's memories with their lifecycle state (newest first)."""
        now = now or datetime.now(UTC)
        items = self._memory.get_all(filters={"user_id": _mem0_user_id(user_id)}, top_k=1000)
        items = [i for i in items.get("results", []) if i.get("user_id") in (None, str(user_id))]
        meta = {
            m.mem0_id: m
            for m in db.scalars(select(MemoryMeta).where(MemoryMeta.user_id == user_id))
        }
        out = []
        for item in items:
            m = meta.get(item["id"]) or self.backfill_meta(db, item["id"], user_id, now)
            out.append(
                {
                    "id": item["id"],
                    "text": item.get("memory", ""),
                    "state": m.state,
                    "strength": round(m.strength, 4),
                    "access_count": m.access_count,
                    "last_accessed_at": m.last_accessed_at,
                    "source": m.source,
                    "superseded_by": m.superseded_by,
                    "created_at": m.created_at,
                }
            )
        db.commit()
        out.sort(key=lambda r: r["created_at"], reverse=True)
        return out

    def owns(self, db: Session, user_id: uuid.UUID, mem_id: str) -> bool:
        if db.scalar(
            select(MemoryMeta.mem0_id).where(
                MemoryMeta.mem0_id == mem_id, MemoryMeta.user_id == user_id
            )
        ):
            return True
        item = self._memory.vector_store.get(vector_id=mem_id)
        return bool(item and (item.payload or {}).get("user_id") == str(user_id))

    def delete_memory(self, db: Session, user_id: uuid.UUID, mem_id: str) -> bool:
        """Delete one memory everywhere: vector, meta row and Mem0 SQLite history."""
        if not self.owns(db, user_id, mem_id):
            return False
        with suppress(ValueError):  # vector already gone: still clean up the rest
            self._memory.delete(mem_id)
        db.query(MemoryMeta).filter(
            MemoryMeta.mem0_id == mem_id, MemoryMeta.user_id == user_id
        ).delete()
        # Mem0's delete WRITES a history row containing the deleted text: purge after it.
        self._purge_history(memory_ids=[mem_id])
        db.commit()
        return True

    def purge_user(self, db: Session, user_id: uuid.UUID) -> dict:
        """Remove every memory artefact of a user: vectors, meta rows, SQLite history and
        buffered messages. (Postgres user data and document chunks: see the caller.)"""
        uid = _mem0_user_id(user_id)
        ids = set(db.scalars(select(MemoryMeta.mem0_id).where(MemoryMeta.user_id == user_id)))
        listed = self._memory.get_all(filters={"user_id": uid}, top_k=10000).get("results", [])
        ids |= {i["id"] for i in listed}
        if listed:
            self._memory.delete_all(user_id=uid)
        db.query(MemoryMeta).filter(MemoryMeta.user_id == user_id).delete()
        history, messages = self._purge_history(memory_ids=sorted(ids), user_id=uid)
        return {"memories": len(ids), "history_rows": history, "message_rows": messages}

    def history_counts(self, user_id: uuid.UUID, memory_ids: list[str]) -> tuple[int, int]:
        """(SQLite history rows for these memory ids, buffered messages for the user)."""
        with self._history() as conn:
            history = 0
            for chunk in _chunks(memory_ids, 500):
                marks = ",".join("?" * len(chunk))
                history += conn.execute(
                    f"SELECT count(*) FROM history WHERE memory_id IN ({marks})", chunk
                ).fetchone()[0]
            messages = conn.execute(
                "SELECT count(*) FROM messages WHERE session_scope = ?",
                (f"user_id={_mem0_user_id(user_id)}",),
            ).fetchone()[0]
        return history, messages

    def _purge_history(
        self, *, memory_ids: list[str], user_id: str | None = None
    ) -> tuple[int, int]:
        history = messages = 0
        with self._history() as conn:
            for chunk in _chunks(memory_ids, 500):
                marks = ",".join("?" * len(chunk))
                history += conn.execute(
                    f"DELETE FROM history WHERE memory_id IN ({marks})", chunk
                ).rowcount
            if user_id is not None:
                messages = conn.execute(
                    "DELETE FROM messages WHERE session_scope = ?", (f"user_id={user_id}",)
                ).rowcount
            conn.commit()
        return history, messages

    @contextmanager
    def _history(self):
        conn = sqlite3.connect(self._memory.config.history_db_path, timeout=30)
        try:
            yield conn
        finally:
            conn.close()

    def health(self, db: Session, user_id: uuid.UUID, *, now: datetime | None = None) -> dict:
        """Counts by state and memories created per day over the last 7 days."""
        now = now or datetime.now(UTC)
        by_state = dict.fromkeys(("active", "stale", "archived", "superseded"), 0)
        for state, count in db.execute(
            select(MemoryMeta.state, func.count())
            .where(MemoryMeta.user_id == user_id)
            .group_by(MemoryMeta.state)
        ):
            by_state[state] = count
        first_day = (now - timedelta(days=6)).astimezone(UTC).date()
        daily = dict.fromkeys((first_day + timedelta(days=i) for i in range(7)), 0)
        start = datetime.combine(first_day, datetime.min.time(), UTC)
        for (created,) in db.execute(
            select(MemoryMeta.created_at).where(
                MemoryMeta.user_id == user_id, MemoryMeta.created_at >= start
            )
        ):
            day = created.astimezone(UTC).date()
            if day in daily:
                daily[day] += 1
        return {
            "total": sum(by_state.values()),
            "by_state": by_state,
            "created_last_7_days": sum(daily.values()),
            "daily": [{"date": d.isoformat(), "created": n} for d, n in daily.items()],
        }
