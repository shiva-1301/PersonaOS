"""Run one chat turn through the LangGraph agent, either all at once or streamed."""

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.confirmations import (
    Outcome,
    confirm_on_yes,
    is_bare_yes,
    reply_text,
    system_note,
)
from app.agent.plan_confirm import plan_if_confirmed
from app.agent.plan_confirm import reply_text as plan_reply
from app.agent.study_plan_guard import plan_if_clearly_requested
from app.agent.study_plan_guard import system_note as plan_note
from app.db.models import CalendarProposal, ChatMessage, ChatSession, User
from app.services.chat_service import ChatTurn, Source, _recent_history, get_owned_session
from app.services.container import Services
from app.services.time_utils import user_zone

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


def _guard_study_plan(
    user: User, message: str, config: dict, history: list[BaseMessage]
) -> tuple[list[dict], dict | None]:
    """Study plans the model would likely fumble are made in code before it runs: a clear
    request (agent/study_plan_guard.py), or a yes to a plan it just proposed
    (agent/plan_confirm.py). The model is told the result.

    Returns (tool results, the plan if it was made from a confirmation)."""
    conf = config["configurable"]
    services = conf["services"]
    # Its own session and transaction, like a tool call.
    with services.session_factory() as tool_db:
        tool_user = tool_db.get(User, user.id)
        result = plan_if_clearly_requested(tool_db, tool_user, services, message, config)
        confirmed_plan = None
        if result is None:
            now = datetime.now(user_zone(tool_user.timezone))
            result = plan_if_confirmed(tool_db, tool_user, services, history, message, config, now)
            if result is not None and "error" not in result:
                confirmed_plan = result
    if result is None:
        return [], None
    conf["system_note"] = "\n\n".join(filter(None, [conf.get("system_note"), plan_note(result)]))
    conf["hidden_tools"] = {PLAN_TOOL}  # the model only describes the plan
    return [{"tool": PLAN_TOOL, "ok": "error" not in result}], confirmed_plan


def _answer_directly(
    db: Session,
    user: User,
    session: ChatSession,
    message: str,
    config: dict,
    reply: str,
    tool_results: list[dict],
) -> ChatTurn:
    """A bare yes that code already carried out: the reply is a template built from
    what was really done, no model call. Saved like any other turn."""
    asked_at = config["configurable"]["asked_at"]
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
        tools_used=[r["tool"] for r in tool_results],
        tool_results=tool_results,
    )


def _with_early(turn: ChatTurn, early: list[dict]) -> ChatTurn:
    """Put tools run in code before the model (a confirmed yes, the plan guard) first."""
    if not early:
        return turn
    return replace(
        turn,
        tools_used=[r["tool"] for r in early] + turn.tools_used,
        tool_results=early + turn.tool_results,
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
        tool_results=list(results),
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
    early = [{"tool": CONFIRM_TOOL, "ok": o.created} for o in confirmed]
    if confirmed and is_bare_yes(message):
        reply = reply_text(confirmed, user.timezone)
        return _answer_directly(db, user, session, message, config, reply, early)
    results, plan = _guard_study_plan(user, message, config, graph_input["messages"][:-1])
    early += results
    if plan and is_bare_yes(message):
        reply = plan_reply(plan, user.timezone)
        return _answer_directly(db, user, session, message, config, reply, early)
    final = services.agent_graph.invoke(graph_input, config=config)
    db.commit()  # chat messages (and the new session) in one transaction
    turn = _with_early(_finish(session, final, started), early)
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
    early = [{"tool": CONFIRM_TOOL, "ok": o.created} for o in confirmed]
    for result in early:
        yield "tool", result
    if confirmed and is_bare_yes(message):
        reply = reply_text(confirmed, user.timezone)
        turn = _answer_directly(db, user, session, message, config, reply, early)
        yield "token", {"text": turn.reply}
        yield "done", turn
        return
    results, plan = _guard_study_plan(user, message, config, graph_input["messages"][:-1])
    for result in results:
        early.append(result)
        yield "tool", result
    if plan and is_bare_yes(message):
        turn = _answer_directly(
            db, user, session, message, config, plan_reply(plan, user.timezone), early
        )
        yield "token", {"text": turn.reply}
        yield "done", turn
        return
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
    turn = _with_early(_finish(session, final, started), early)
    turn.pending_confirmations.extend(
        _pending_confirmations(db, user.id, config["configurable"]["asked_at"])
    )
    yield "done", turn
