"""Run one chat turn through the LangGraph agent, either all at once or streamed."""

import logging
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.db.models import ChatSession, User
from app.services.chat_service import ChatTurn, Source, _recent_history, get_owned_session
from app.services.container import Services

logger = logging.getLogger(__name__)

# Bounds the whole graph run (each node visit is one step). 5 tool rounds need ~13.
RECURSION_LIMIT = 30


def _prepare(
    db: Session, user: User, message: str, session_id: uuid.UUID | None, services: Services
) -> tuple[ChatSession, dict, dict]:
    """Session (existing and owned, or new), graph input, and graph config."""
    if session_id is not None:
        session = get_owned_session(db, user.id, session_id)
    else:
        session = ChatSession(user_id=user.id, title=message.strip()[:60] or None)
        db.add(session)
        db.flush()
    history = _recent_history(db, session, services.settings.CHAT_HISTORY_LIMIT)
    graph_input = {
        "messages": [*history, HumanMessage(message)],
        "user_id": str(user.id),
        "session_id": str(session.id),
    }
    config = {
        "configurable": {
            # The ONLY source of identity for tools: the verified user, never the model.
            "user_id": user.id,
            "session_id": session.id,
            "services": services,
            "session_factory": services.session_factory,
            "db": db,
            "user_message": message,
            "asked_at": datetime.now(UTC),
            # Scratch space shared by this turn's tool calls (e.g. duplicate guards).
            "turn": {},
        },
        "recursion_limit": RECURSION_LIMIT,
    }
    return session, graph_input, config


def _finish(session: ChatSession, final: dict[str, Any], started: float) -> ChatTurn:
    results = final.get("tool_results", [])
    tools_used = [r["tool"] for r in results]
    logger.info(
        "Agent turn",
        extra={
            "ms": round((time.perf_counter() - started) * 1000),
            "tool_rounds": final.get("iteration_count", 0),
            "tools": tools_used,
            "tool_failures": sum(1 for r in results if not r["ok"]),
            "memories_used": len(final.get("memories", [])),
            "chunks_used": len(final.get("retrieved_chunks", [])),
        },
    )
    return ChatTurn(
        session_id=session.id,
        user_message_id=uuid.UUID(final["user_message_id"]),
        reply=final["reply"],
        memories_used=len(final.get("memories", [])),
        sources=[
            Source(uuid.UUID(c["document_id"]), c["filename"], c["chunk_index"])
            for c in final.get("retrieved_chunks", [])
        ],
        tools_used=tools_used,
    )


def run_agent_turn(
    db: Session,
    user: User,
    message: str,
    session_id: uuid.UUID | None,
    services: Services,
) -> ChatTurn:
    session, graph_input, config = _prepare(db, user, message, session_id, services)
    started = time.perf_counter()
    final = services.agent_graph.invoke(graph_input, config=config)
    db.commit()  # chat messages (and the new session) in one transaction
    return _finish(session, final, started)


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content if isinstance(content, str) else ""


def stream_agent_turn(
    db: Session,
    user: User,
    message: str,
    session_id: uuid.UUID | None,
    services: Services,
) -> Iterator[tuple[str, Any]]:
    """Yield (event, data) while the agent runs:

    token  {"text": ...}           piece of the answer as the model writes it
    reset  {}                      discard streamed text: the model went on to call a tool
    tool   {"tool": ..., "ok": ..} a tool call finished
    done   ChatTurn                final result, after everything is committed
    """
    session, graph_input, config = _prepare(db, user, message, session_id, services)
    started = time.perf_counter()
    final: dict[str, Any] = {}
    streamed = False
    for mode, chunk in services.agent_graph.stream(
        graph_input, config=config, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            msg, meta = chunk
            # Only the agent node's own LLM calls; tools (e.g. summaries) call models too.
            if meta.get("langgraph_node") != "agent":
                continue
            if getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None):
                continue
            text = _text(msg.content)
            if text:
                streamed = True
                yield "token", {"text": text}
        else:
            for node, update in chunk.items():
                if not update:
                    continue
                if node == "tools":
                    if streamed:
                        yield "reset", {}
                        streamed = False
                    known = len(final.get("tool_results", []))
                    for result in update["tool_results"][known:]:
                        yield "tool", result
                final.update(update)
    db.commit()
    yield "done", _finish(session, final, started)
