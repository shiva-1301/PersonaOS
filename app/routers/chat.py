import json
import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import select, update
from starlette.background import BackgroundTask

from app.agent.runner import run_agent_turn, stream_agent_turn
from app.db.models import ChatMessage, ChatSession, User
from app.deps import CurrentUser, DbSession
from app.errors import error_body
from app.schemas.chat import (
    ChatIn,
    ChatOut,
    MessageOut,
    SessionDetailOut,
    SessionOut,
    SourceOut,
)
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


def _check_length(services, message: str) -> None:
    if len(message) > services.settings.MAX_MESSAGE_CHARS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"message: at most {services.settings.MAX_MESSAGE_CHARS} characters",
        )


def _chat_out(turn) -> ChatOut:
    return ChatOut(
        session_id=turn.session_id,
        message_id=turn.user_message_id,
        reply=turn.reply,
        memories_used=turn.memories_used,
        memory_status="pending",
        sources=[
            SourceOut(document_id=s.document_id, filename=s.filename, chunk_index=s.chunk_index)
            for s in turn.sources
        ],
        tools_used=turn.tools_used,
        pending_confirmations=turn.pending_confirmations,
    )


def _extraction_input(message: str) -> list[dict[str, str]]:
    # Only the user's own words are sent for extraction. Including the assistant reply
    # made the extractor invent "facts" from the assistant's questions.
    return [{"role": "user", "content": message}]


def _error_for(exc: Exception) -> HTTPException:
    """Map a failed turn to a user-safe HTTP error (internals are only logged)."""
    if isinstance(exc, SessionNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND)
    if isinstance(exc, LLMConfigError):
        logger.error("LLM not configured")
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The assistant is not configured")
    if isinstance(exc, ModelRateLimitError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The assistant is busy (rate limited). Please try again in a minute.",
            headers={"Retry-After": "60"},
        )
    logger.error("LLM call failed", exc_info=exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "The assistant is temporarily unavailable")


@router.post("", response_model=ChatOut)
def chat(
    body: ChatIn,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
    db: DbSession,
) -> ChatOut:
    services = request.app.state.services
    _check_length(services, body.message)
    try:
        run = run_agent_turn if services.settings.CHAT_MODE == "agent" else run_chat_turn
        turn = run(db, user, body.message, body.session_id, services)
    except Exception as exc:
        raise _error_for(exc) from None
    background.add_task(
        save_turn_in_background,
        request.app,
        user.id,
        turn.user_message_id,
        _extraction_input(body.message),
    )
    return _chat_out(turn)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def chat_stream(body: ChatIn, request: Request, user: CurrentUser, db: DbSession):
    """Same as POST /chat, streamed as Server-Sent Events (agent mode):

    - `token` {"text"}: the next piece of the answer
    - `reset` {}: discard text streamed so far (the model went on to use a tool)
    - `tool` {"tool", "ok"}: a tool call finished
    - `done`: the same JSON body as POST /chat
    - `error` {"error": {"code", "message"}}: the turn failed; nothing was saved
    """
    app = request.app
    services = app.state.services
    _check_length(services, body.message)
    if body.session_id is not None:  # fail fast with a normal 404 before streaming
        try:
            get_owned_session(db, user.id, body.session_id)
        except SessionNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_NOT_FOUND) from None
    user_id, message, session_id = user.id, body.message, body.session_id
    finished: dict = {}

    def events():
        # Own DB session: the request's session is closed once streaming starts.
        with app.state.session_factory() as stream_db:
            try:
                stream_user = stream_db.get(User, user_id)
                for event, data in stream_agent_turn(
                    stream_db, stream_user, message, session_id, services
                ):
                    if event == "done":
                        finished["turn"] = data
                        yield _sse("done", _chat_out(data).model_dump(mode="json"))
                    else:
                        yield _sse(event, data)
            except Exception as exc:
                stream_db.rollback()
                err = _error_for(exc)
                code = {404: "not_found", 502: "upstream_error", 503: "service_unavailable"}
                yield _sse("error", error_body(code.get(err.status_code, "error"), err.detail))

    def after_stream():
        if "turn" in finished:
            save_turn_in_background(
                app, user_id, finished["turn"].user_message_id, _extraction_input(message)
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(after_stream),
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
