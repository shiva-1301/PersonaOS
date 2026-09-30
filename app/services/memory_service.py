"""Long-term user memory: Mem0 (extraction + vectors in Chroma) plus `memory_meta` in
Postgres for the lifecycle layer.

Phase 3: lifecycle is stubbed. `save_turn` records new memories as active with strength
1.0; `recall` drops archived/superseded ones and ranks by relevance x strength.
Reinforcement, decay and supersession arrive in Phase 7.

Isolation: every Mem0 call is scoped by `user_id` (the users.id UUID as a string) and
every `memory_meta` query filters on the same user. `_mem0_user_id` is the only place
that converts the id.
"""

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import MemoryMeta
from app.services.llm import embedding_space_id

logger = logging.getLogger(__name__)

EXTRACTION_INSTRUCTIONS = """\
Only extract durable facts that the USER states about themselves: preferences, habits,
schedule, goals, studies, work, constraints and personal details they volunteer.
Do NOT store the assistant's suggestions, general knowledge, greetings, or one-off
questions. Never store passwords, API keys, card numbers or other secrets.
Write each memory as a short third-person statement about the user."""

HIDDEN_STATES = ("archived", "superseded")


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


class MemoryService:
    def __init__(self, memory: Any):
        self._memory = memory

    # ------------------------------------------------------------------ write path

    def save_turn(
        self,
        db: Session,
        user_id: uuid.UUID,
        messages: list[dict[str, str]],
        *,
        source: str = "chat",
    ) -> list[str]:
        """Extract memories from a conversation turn and register them in memory_meta."""
        result = self._memory.add(
            messages, user_id=_mem0_user_id(user_id), metadata={"source": source}
        )
        events = result.get("results", []) if isinstance(result, dict) else []
        now = datetime.now(UTC)
        saved: list[str] = []
        for item in events:
            mem_id, event = item.get("id"), item.get("event")
            if not mem_id:
                continue
            if event in ("ADD", "UPDATE"):
                self._upsert_meta(db, mem_id, user_id, source, now)
                saved.append(mem_id)
            elif event == "DELETE":
                db.query(MemoryMeta).filter(
                    MemoryMeta.mem0_id == mem_id, MemoryMeta.user_id == user_id
                ).delete()
        db.commit()
        return saved

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

    # ------------------------------------------------------------------ read path

    def recall(
        self, db: Session, user_id: uuid.UUID, query: str, k: int = 5
    ) -> list[RecalledMemory]:
        query = (query or "").strip()
        if not query:
            return []
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

        meta = {
            m.mem0_id: m
            for m in db.scalars(
                select(MemoryMeta).where(
                    MemoryMeta.user_id == user_id,
                    MemoryMeta.mem0_id.in_([h["id"] for h in hits]),
                )
            )
        }
        recalled = []
        for h in hits:
            m = meta.get(h["id"])
            # A Mem0 memory without a meta row is treated as active (backfilled in Phase 7).
            if m is not None and m.state in HIDDEN_STATES:
                continue
            recalled.append(
                RecalledMemory(
                    id=h["id"],
                    text=h.get("memory", ""),
                    relevance=float(h.get("score") or 0.0),
                    strength=m.strength if m is not None else 1.0,
                )
            )
        recalled.sort(key=lambda r: r.score, reverse=True)
        return recalled[:k]
