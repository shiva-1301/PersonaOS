"""Delete everything PersonaOS stores about a user, and prove nothing is left.

Stores and how each is cleared:
- Mem0 vectors (Chroma `personaos_mem0__*`)     -> Mem0 delete_all(user_id)
- Mem0 SQLite history + buffered messages        -> purged by memory id / session scope
  (Mem0's own delete WRITES history rows with the deleted text, so this runs after it)
- Document chunks (Chroma `personaos_documents__*`) -> delete where user_id
- Postgres: the users row; everything else cascades (sessions, messages, goals, tasks,
  documents, memory_meta, google_tokens)
External stores are cleared first: if anything fails, the user row still exists and the
request can simply be repeated.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ChatMessage,
    ChatSession,
    Document,
    Goal,
    GoogleToken,
    MemoryMeta,
    Task,
    User,
)
from app.services.container import Services

CONFIRM_PHRASE = "DELETE MY DATA"
USER_TABLES = (ChatSession, ChatMessage, Goal, Task, Document, MemoryMeta, GoogleToken)


def _postgres_counts(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    counts = {
        model.__tablename__: db.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        for model in USER_TABLES
    }
    counts["users"] = db.scalar(select(func.count()).select_from(User).where(User.id == user_id))
    return counts


def remaining_data(
    db: Session, services: Services, user_id: uuid.UUID, memory_ids: list[str]
) -> dict[str, int]:
    """Everything still stored for this user, across all stores (all zero = erased)."""
    memory = services.memory
    mem0_vectors = len(
        memory.vector_store.collection.get(where={"user_id": str(user_id)}, include=[])["ids"]
    )
    history_rows, message_rows = memory.history_counts(user_id, memory_ids)
    return {
        **{f"postgres.{k}": v for k, v in _postgres_counts(db, user_id).items()},
        "mem0.vectors": mem0_vectors,
        "mem0.sqlite_history": history_rows,
        "mem0.sqlite_messages": message_rows,
        "chroma.document_chunks": services.rag.count(user_id),
    }


def delete_all_user_data(db: Session, services: Services, user: User) -> dict:
    user_id = user.id
    deleted = {f"postgres.{k}": v for k, v in _postgres_counts(db, user_id).items()}
    memory = services.memory.purge_user(db, user_id)
    chunks = services.rag.count(user_id)
    services.rag.collection.delete(where={"user_id": str(user_id)})
    db.delete(user)
    db.commit()
    return {
        **deleted,
        "mem0.memories": memory["memories"],
        "mem0.sqlite_history": memory["history_rows"],
        "mem0.sqlite_messages": memory["message_rows"],
        "chroma.document_chunks": chunks,
    }
