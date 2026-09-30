"""Plain chat (no agent yet): session -> history -> recall -> LLM -> persist.

Memory extraction (`save_turn`) runs after the response is sent; see routers/chat.py.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.prompts import build_system_prompt
from app.db.models import ChatMessage, ChatSession, User
from app.services.container import Services
from app.services.llm import invoke_with_backoff

logger = logging.getLogger(__name__)

TITLE_CHARS = 60


class SessionNotFound(Exception):
    pass


@dataclass(frozen=True)
class ChatTurn:
    session_id: uuid.UUID
    user_message_id: uuid.UUID
    reply: str
    memories_used: int


def get_owned_session(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> ChatSession:
    session = db.scalar(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    if session is None:
        raise SessionNotFound
    return session


def _recent_history(db: Session, session: ChatSession, limit: int) -> list[BaseMessage]:
    if limit == 0:
        return []
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id, ChatMessage.user_id == session.user_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    ).all()
    history: list[BaseMessage] = []
    for m in reversed(rows):
        if m.role == "user":
            history.append(HumanMessage(m.content))
        elif m.role == "assistant":
            history.append(AIMessage(m.content))
    return history


def _user_now(user: User) -> datetime:
    try:
        return datetime.now(ZoneInfo(user.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(UTC)


def _text(content) -> str:
    if isinstance(content, list):  # content blocks (e.g. Gemini)
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def run_chat_turn(
    db: Session,
    user: User,
    message: str,
    session_id: uuid.UUID | None,
    services: Services,
) -> ChatTurn:
    settings = services.settings
    if session_id is not None:
        session = get_owned_session(db, user.id, session_id)
    else:
        session = ChatSession(user_id=user.id, title=message.strip()[:TITLE_CHARS] or None)
        db.add(session)
        db.flush()

    history = _recent_history(db, session, settings.CHAT_HISTORY_LIMIT)
    # Explicit timestamps: Postgres now() is per-transaction, so both messages of a turn
    # would otherwise share one timestamp and could come back in either order.
    asked_at = datetime.now(UTC)

    try:
        memories = services.memory.recall(db, user.id, message, k=settings.MEMORY_RECALL_K)
    except Exception as exc:
        # Chat still works without memory (e.g. Ollama down); the failure is logged.
        logger.warning("Memory recall failed", extra={"error": type(exc).__name__})
        memories = []

    prompt: list[BaseMessage] = [
        SystemMessage(
            build_system_prompt(
                [m.text for m in memories], now=_user_now(user), timezone=user.timezone
            )
        ),
        *history,
        HumanMessage(message),
    ]

    started = time.perf_counter()
    result = invoke_with_backoff(
        services.chat_model, prompt, attempts=settings.LLM_RATE_LIMIT_ATTEMPTS
    )
    reply = _text(result.content).strip()
    answered_at = datetime.now(UTC)
    logger.info(
        "Chat turn",
        extra={
            "llm_ms": round((time.perf_counter() - started) * 1000),
            "memories_used": len(memories),
            "history_messages": len(history),
        },
    )

    user_message = ChatMessage(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=message,
        created_at=asked_at,
        # Memory extraction for this turn runs after the response (routers/chat.py).
        memory_status="pending",
    )
    db.add_all(
        [
            user_message,
            ChatMessage(
                session_id=session.id,
                user_id=user.id,
                role="assistant",
                content=reply,
                created_at=answered_at,
            ),
        ]
    )
    session.updated_at = answered_at
    db.commit()
    return ChatTurn(
        session_id=session.id,
        user_message_id=user_message.id,
        reply=reply,
        memories_used=len(memories),
    )
