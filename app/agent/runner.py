"""Run one chat turn through the LangGraph agent, either all at once or streamed."""

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.confirmations import (
    Outcome,
    confirm_on_yes,
    is_bare_yes,
    reply_text,
    system_note,
)
from app.agent.study_plan_guard import plan_if_clearly_requested
from app.agent.study_plan_guard import system_note as plan_note
from app.db.models import CalendarProposal, ChatMessage, ChatSession, User
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


def _pending_confirmations(db: Session, user_id, since) -> list[dict]:
    rows = db.scalars(
        select(CalendarProposal).where(
            CalendarProposal.user_id == user_id,
            CalendarProposal.status == "pending",
            CalendarProposal.created_at >= since,
        )
    )
    return [
        {"proposal_id": str(p.id), "title": p.title, "start_at": p.start_at, "end_at": p.end_at}
        for p in rows
    ]


CONFIRM_TOOL = "confirm_calendar_event"
PLAN_TOOL = "generate_study_plan"


def _confirm_previous_proposals(
    db: Session, user: User, session: ChatSession, message: str, config: dict, existing: bool
) -> list[Outcome]:
    """A clear yes to the previous turn's calendar proposals is applied in code, before
    the model runs (agent/confirmations.py). A longer message still goes to the agent,
    which is told what already happened."""
    if not existing:
        return []
    conf = config["configurable"]
    outcomes = confirm_on_yes(
        db, user, session.id, message, conf["asked_at"], conf["services"].google
    )
    if outcomes and not is_bare_yes(message):
        conf["system_note"] = system_note(outcomes)
    return outcomes


def _guard_study_plan(user: User, message: str, config: dict) -> list[dict]:
    """A clear study-plan request is planned in code before the model runs
    (agent/study_plan_guard.py); the model is told the result. Returns tool results."""
    conf = config["configurable"]
    services = conf["services"]
    # Its own session and transaction, like a tool call.
    with services.session_factory() as tool_db:
        result = plan_if_clearly_requested(
            tool_db, tool_db.get(User, user.id), services, message, config
        )
    if result is None:
        return []
    conf["system_note"] = "\n\n".join(filter(None, [conf.get("system_note"), plan_note(result)]))
    conf["hidden_tools"] = {PLAN_TOOL}  # the model only describes the plan
    return [{"tool": PLAN_TOOL, "ok": "error" not in result}]


def _answer_directly(
    db: Session, user: User, session: ChatSession, message: str, config: dict, outcomes
) -> ChatTurn:
    """A bare yes: the reply is a template, no model call. Saved like any other turn."""
    asked_at = config["configurable"]["asked_at"]
    reply = reply_text(outcomes, user.timezone)
    user_msg = ChatMessage(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=message,
        created_at=asked_at,
        memory_status="pending",
    )
    answered_at = datetime.now(UTC)
    db.add_all(
        [
            user_msg,
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
        user_message_id=user_msg.id,
        reply=reply,
        memories_used=0,
        sources=[],
        tools_used=[CONFIRM_TOOL] * len(outcomes),
    )


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
    confirmed = _confirm_previous_proposals(
        db, user, session, message, config, existing=session_id is not None
    )
    if confirmed and is_bare_yes(message):
        return _answer_directly(db, user, session, message, config, confirmed)
    early = [CONFIRM_TOOL] * len(confirmed)
    early += [r["tool"] for r in _guard_study_plan(user, message, config)]
    final = services.agent_graph.invoke(graph_input, config=config)
    db.commit()  # chat messages (and the new session) in one transaction
    turn = _finish(session, final, started)
    if early:
        turn = replace(turn, tools_used=early + turn.tools_used)
    turn.pending_confirmations.extend(
        _pending_confirmations(db, user.id, config["configurable"]["asked_at"])
    )
    return turn


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
    confirmed = _confirm_previous_proposals(
        db, user, session, message, config, existing=session_id is not None
    )
    for outcome in confirmed:
        yield "tool", {"tool": CONFIRM_TOOL, "ok": outcome.created}
    if confirmed and is_bare_yes(message):
        turn = _answer_directly(db, user, session, message, config, confirmed)
        yield "token", {"text": turn.reply}
        yield "done", turn
        return
    early = [CONFIRM_TOOL] * len(confirmed)
    for result in _guard_study_plan(user, message, config):
        early.append(result["tool"])
        yield "tool", result
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
    turn = _finish(session, final, started)
    if early:
        turn = replace(turn, tools_used=early + turn.tools_used)
    turn.pending_confirmations.extend(
        _pending_confirmations(db, user.id, config["configurable"]["asked_at"])
    )
    yield "done", turn
