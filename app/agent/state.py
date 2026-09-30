"""State carried through one agent turn."""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # Conversation for this turn: system prompt, recent history, the new user message,
    # then agent/tool messages as the graph runs.
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    session_id: str
    memories: list[str]  # recalled facts injected into the system prompt
    retrieved_chunks: list[dict]  # document excerpts injected (for citations)
    tool_results: list[dict]  # [{"tool": name, "ok": bool}] per call, in order
    iteration_count: int  # tool rounds so far (capped, see graph.MAX_TOOL_ROUNDS)
    system_prompt: str  # built by load_context (memories, excerpts, time, tool rules)
    user_message_id: str  # set by save_memory
    reply: str  # final answer text, set by save_memory
