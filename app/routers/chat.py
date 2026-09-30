import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import select, update

from app.db.models import ChatMessage, ChatSession
from app.deps import CurrentUser, DbSession
from app.schemas.chat import ChatIn, ChatOut, MessageOut, SessionDetailOut, SessionOut
from app.services.chat_service import SessionNotFound, get_owned_session, run_chat_turn
from app.services.llm import LLMConfigError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

SESSION_NOT_FOUND = "Chat session not found"


def save_turn_in_background(
    app, user_id: uuid.UUID, message_id: uuid.UUID, messages: list[dict[str, str]]
) -> None:
    """Runs after the response is sent, with its own DB session.

    Records the outcome on the user message (`memory_status`: done | failed) so clients
    can see when a turn's memories are available. Failures are logged, never raised.
    """
    started = time.perf_counter()
    status_value, saved = "failed", []
    try:
        with app.state.session_factory() as db:
            saved = app.state.services.memory.save_turn(db, user_id, messages)
        status_value = "done"
    except Exception:
        logger.exception("Memory save failed", extra={"user_id": str(user_id)})
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            with app.state.session_factory() as db:
                db.execute(
                    update(ChatMessage)
                    .where(ChatMessage.id == message_id, ChatMessage.user_id == user_id)
                    .values(memory_status=status_value)
                )
                db.commit()
        except Exception:
            logger.exception("Could not record memory status")
    if status_value == "done":
        logger.info("Memory saved", extra={"new_memories": len(saved), "extraction_ms": elapsed_ms})


@router.post("", response_model=ChatOut)
def chat(
    body: ChatIn,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
    db: DbSession,
) -> ChatOut:
    services = request.app.state.services
    if len(body.message) > services.settings.MAX_MESSAGE_CHARS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"message: at most {services.settings.MAX_MESSAGE_CHARS} characters",
        )
    try:
        turn = run_chat_turn(db, user, body.message, body.session_id, services)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND) from None
    except LLMConfigError:
        logger.exception("LLM not configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The assistant is not configured"
        ) from None
    except ModelRateLimitError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The assistant is busy (rate limited). Please try again in a minute.",
            headers={"Retry-After": "60"},
        ) from None
    except Exception:
        logger.exception("LLM call failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The assistant is temporarily unavailable"
        ) from None

    # Only the user's own words are sent for extraction. Including the assistant reply
    # made the extractor invent "facts" from the assistant's questions.
    background.add_task(
        save_turn_in_background,
        request.app,
        user.id,
        turn.user_message_id,
        [{"role": "user", "content": body.message}],
    )
    return ChatOut(
        session_id=turn.session_id,
        message_id=turn.user_message_id,
        reply=turn.reply,
        memories_used=turn.memories_used,
        memory_status="pending",
    )


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(user: CurrentUser, db: DbSession) -> list[ChatSession]:
    return list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user.id)
            .order_by(ChatSession.updated_at.desc())
        )
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
def get_session(session_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SessionDetailOut:
    try:
        session = get_owned_session(db, user.id, session_id)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND) from None
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id, ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    return SessionDetailOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[MessageOut.model_validate(m) for m in messages],
    )
