"""The PersonaOS agent: START -> load_context -> agent <-> tools -> save_memory -> END.

Runtime context travels in RunnableConfig["configurable"], never through the model:
  user_id (UUID from the verified token), services, session_factory (for tools),
  db (the turn's own session: load_context + save_memory, committed by the runner).
"""

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.prompts import TOOL_LIMIT_NOTE, Excerpt, build_system_prompt
from app.agent.state import AgentState
from app.agent.tools import ALL_TOOLS
from app.db.models import ChatMessage, ChatSession, User
from app.services.llm import invoke_with_backoff
from app.services.time_utils import user_zone

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
# Retries when the model returns nothing at all (seen with qwen2.5:7b via Ollama).
EMPTY_RESPONSE_ATTEMPTS = 3
EMPTY_FALLBACK = "Sorry, I couldn't produce an answer just now. Please try again."


def _content_text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content if isinstance(content, str) else ""


def _conf(config: RunnableConfig) -> dict:
    return config["configurable"]


def _tool_error(exc: Exception) -> str:
    # Unexpected failures inside a tool: the model gets a neutral message, not internals.
    return "Error: the tool failed unexpectedly. Tell the user this action did not work."


def build_agent_graph(tools=ALL_TOOLS):
    tool_node = ToolNode(tools, handle_tool_errors=_tool_error)

    def load_context(state: AgentState, config: RunnableConfig) -> dict:
        conf = _conf(config)
        services, db = conf["services"], conf["db"]
        settings = services.settings
        user = db.get(User, conf["user_id"])
        question = str(state["messages"][-1].content)

        try:
            memories = services.memory.recall(db, user.id, question, k=settings.MEMORY_RECALL_K)
        except Exception as exc:
            logger.warning("Memory recall failed", extra={"error": type(exc).__name__})
            memories = []
        try:
            chunks = services.rag.retrieve(
                user.id,
                question,
                k=settings.RAG_TOP_K,
                min_relevance=settings.RAG_MIN_RELEVANCE,
                relative_margin=settings.RAG_RELATIVE_MARGIN,
            )
        except Exception as exc:
            logger.warning("Document retrieval failed", extra={"error": type(exc).__name__})
            chunks = []

        system = build_system_prompt(
            [m.text for m in memories],
            now=datetime.now(user_zone(user.timezone)),
            timezone=user.timezone,
            excerpts=[Excerpt(c.filename, c.chunk_index, c.text) for c in chunks],
            agent=True,
        )
        return {
            "system_prompt": system,
            "memories": [m.text for m in memories],
            "retrieved_chunks": [
                {
                    "document_id": str(c.document_id),
                    "filename": c.filename,
                    "chunk_index": c.chunk_index,
                }
                for c in chunks
            ],
            "tool_results": [],
            "iteration_count": 0,
        }

    def agent(state: AgentState, config: RunnableConfig) -> dict:
        services = _conf(config)["services"]
        rounds = state.get("iteration_count", 0)
        system = state["system_prompt"]
        if rounds >= MAX_TOOL_ROUNDS:
            model = services.chat_model  # no tools bound: it must answer now
            system = f"{system}\n\n{TOOL_LIMIT_NOTE}"
        else:
            model = services.chat_model.bind_tools(tools)
        for attempt in range(1, EMPTY_RESPONSE_ATTEMPTS + 1):
            response = invoke_with_backoff(
                model,
                [SystemMessage(system), *state["messages"]],
                attempts=services.settings.LLM_RATE_LIMIT_ATTEMPTS,
            )
            if response.tool_calls or _content_text(response.content).strip():
                break
            # Local models occasionally return a completely empty message (no text, no
            # tool call); a fresh sample almost always works.
            logger.warning("Empty model response; retrying", extra={"attempt": attempt})
        else:
            response = AIMessage(content=EMPTY_FALLBACK)
        if rounds >= MAX_TOOL_ROUNDS and getattr(response, "tool_calls", None):
            # Defence in depth: a model that still emits tool calls is not obeyed.
            response = AIMessage(content=response.content or "I couldn't finish that.")
        return {"messages": [response]}

    def run_tools(state: AgentState, config: RunnableConfig) -> dict:
        result = tool_node.invoke(state, config)
        outcomes = []
        for msg in result["messages"]:
            if isinstance(msg, ToolMessage):
                failed = msg.status == "error" or str(msg.content).startswith('{"error"')
                outcomes.append({"tool": msg.name, "ok": not failed})
        return {
            "messages": result["messages"],
            "tool_results": [*state.get("tool_results", []), *outcomes],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "save_memory"

    def save_memory(state: AgentState, config: RunnableConfig) -> dict:
        """Persist the user message (memory_status=pending) and the final reply.

        Memory extraction itself runs after the HTTP response (routers/chat.py), so the
        reply is not delayed; the runner commits this in the turn's transaction."""
        conf = _conf(config)
        db = conf["db"]
        session = db.get(ChatSession, conf["session_id"])
        reply = state["messages"][-1]
        text = reply.content
        if isinstance(text, list):
            text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
        user_msg = ChatMessage(
            session_id=session.id,
            user_id=conf["user_id"],
            role="user",
            content=conf["user_message"],
            created_at=conf["asked_at"],
            memory_status="pending",
        )
        answered_at = datetime.now(UTC)
        db.add_all(
            [
                user_msg,
                ChatMessage(
                    session_id=session.id,
                    user_id=conf["user_id"],
                    role="assistant",
                    content=str(text).strip(),
                    created_at=answered_at,
                ),
            ]
        )
        session.updated_at = answered_at
        db.flush()
        return {"user_message_id": str(user_msg.id), "reply": str(text).strip()}

    graph = StateGraph(AgentState)
    graph.add_node("load_context", load_context)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("save_memory", save_memory)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", "save_memory": "save_memory"})
    graph.add_edge("tools", "agent")
    graph.add_edge("save_memory", END)
    return graph.compile()
