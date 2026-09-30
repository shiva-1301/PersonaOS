"""SQLAlchemy models. Every user-owned table carries `user_id` with ON DELETE CASCADE."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic migrations are stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

GOAL_STATUSES = ("active", "completed", "paused")
TASK_STATUSES = ("todo", "doing", "done")
DOCUMENT_STATUSES = ("processing", "ready", "failed")
DOCUMENT_SOURCES = ("upload", "drive")
MESSAGE_ROLES = ("user", "assistant", "system", "tool")
MEMORY_STATES = ("active", "stale", "archived", "superseded")
MEMORY_SOURCES = ("chat", "document", "manual")
# Background memory extraction for a user message: pending -> done | failed.
MEMORY_STATUSES = ("pending", "done", "failed")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    auth_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    created_at: Mapped[datetime] = _created_at()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (Index("ix_chat_sessions_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        _one_of("role", MESSAGE_ROLES, "role"),
        # NULL passes a CHECK in Postgres, so assistant rows (no status) are allowed.
        _one_of("memory_status", MEMORY_STATUSES, "memory_status"),
        Index("ix_chat_messages_session_id_created_at", "session_id", "created_at"),
        Index("ix_chat_messages_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised owner so every table can be filtered by user_id directly.
    user_id: Mapped[uuid.UUID] = _user_fk()
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Set on user messages only: state of the background memory extraction for this turn.
    memory_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created_at()


class Goal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        _one_of("status", GOAL_STATUSES, "status"),
        Index("ix_goals_user_id_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        _one_of("status", TASK_STATUSES, "status"),
        CheckConstraint("est_minutes IS NULL OR est_minutes > 0", name="est_minutes_positive"),
        Index("ix_tasks_user_id_status", "user_id", "status"),
        Index("ix_tasks_user_id_due_at", "user_id", "due_at"),
        Index("ix_tasks_goal_id", "goal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    # Deleting a goal keeps its tasks (goal link cleared) rather than silently deleting work.
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("goals.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    est_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="todo")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        _one_of("status", DOCUMENT_STATUSES, "status"),
        _one_of("source", DOCUMENT_SOURCES, "source"),
        Index("ix_documents_user_id_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = _user_fk()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="upload")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="processing")
    error: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class MemoryMeta(Base):
    __tablename__ = "memory_meta"
    __table_args__ = (
        _one_of("state", MEMORY_STATES, "state"),
        _one_of("source", MEMORY_SOURCES, "source"),
        CheckConstraint("strength >= 0 AND strength <= 1", name="strength_range"),
        Index("ix_memory_meta_user_id_state", "user_id", "state"),
    )

    mem0_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    superseded_by: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="chat")
    created_at: Mapped[datetime] = _created_at()


class GoogleToken(Base):
    """Generalised `calendar_tokens`: one Google connection per user, scopes recorded."""

    __tablename__ = "google_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    needs_reconnect: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
