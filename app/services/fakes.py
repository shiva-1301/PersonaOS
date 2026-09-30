"""Deterministic offline models for tests and CI (LLM_PROVIDER / EMBEDDING_PROVIDER = fake).

- `HashingEmbeddings`: bag-of-words feature hashing, so texts sharing words are similar.
- `FakeChatModel`: answers Mem0 extraction prompts with the user's own statements as facts,
  and answers chat prompts with an echo that exposes the memories it was given, so tests
  can assert exactly what reached the prompt.
"""

import hashlib
import json
import math
import re
from datetime import date, timedelta
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.prompts import MEMORIES_HEADER, PLAN_MARKER

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "i",
        "me",
        "my",
        "you",
        "your",
        "is",
        "am",
        "are",
        "was",
        "be",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "it",
        "this",
        "that",
        "do",
        "what",
        "when",
        "where",
        "how",
        "should",
    }
)


class HashingEmbeddings(Embeddings):
    def __init__(self, dim: int = 256):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in _WORD.findall(text.lower()):
            if word in _STOPWORDS:
                continue
            digest = hashlib.sha256(word.encode()).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            vec[idx] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


EXTRACTION_MARKER = "Memory Extractor"


def _content(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def fake_extract(prompt: str) -> str:
    """Treat each user line under '## New Messages' as one fact."""
    section = prompt.split("## New Messages", 1)[-1].split("\n## ", 1)[0]
    facts = [
        line[len("user: ") :].strip()
        for line in section.splitlines()
        if line.startswith("user: ") and line[len("user: ") :].strip()
    ]
    return json.dumps({"memory": [{"text": f} for f in facts]})


def fake_reply(messages: list[BaseMessage]) -> str:
    system = "\n".join(_content(m) for m in messages if m.type == "system")
    memories: list[str] = []
    if MEMORIES_HEADER in system:
        block = system.split(MEMORIES_HEADER, 1)[1].split("\n## ", 1)[0]
        memories = [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]
    sources = re.findall(r'UNTRUSTED_DOCUMENT_EXCERPT source="([^"]*)"', system)
    last_user = next((_content(m) for m in reversed(messages) if m.type == "human"), "")
    history = sum(1 for m in messages if m.type in ("human", "ai")) - 1
    return f"ECHO: {last_user} | MEMORIES: {memories} | HISTORY: {history} | SOURCES: {sources}"


def fake_plan(prompt: str) -> str:
    """One 60-minute session per week at 18:00 local, across the requested window."""
    window = re.search(r"Window: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", prompt)
    now = re.search(r"Now: (\d{4}-\d{2}-\d{2})", prompt)
    if not window:
        return json.dumps({"tasks": []})
    start = max(date.fromisoformat(window.group(1)), date.fromisoformat(now.group(1)))
    end = date.fromisoformat(window.group(2))
    tasks, day, n = [], start, 1
    while day <= end and n <= 12:
        tasks.append(
            {
                "title": f"Session {n}",
                "notes": "Study and review.",
                "due_at": f"{day.isoformat()}T18:00",
                "est_minutes": 60,
            }
        )
        day += timedelta(days=7)
        n += 1
    return json.dumps({"tasks": tasks})


def _text_of(messages: list[BaseMessage], kind: str) -> str:
    return "\n".join(_content(m) for m in messages if m.type == kind)


class FakeChatModel(BaseChatModel):
    """Never calls tools; bind_tools is accepted so the agent graph can run offline."""

    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "personaos-fake"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = [getattr(t, "name", str(t)) for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system = _text_of(messages, "system")
        if EXTRACTION_MARKER in system:
            text = fake_extract(_text_of(messages, "human"))
        elif PLAN_MARKER in system:
            text = fake_plan(_content(next(m for m in messages if m.type == "human")))
        else:
            text = fake_reply(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


class ScriptedChatModel(BaseChatModel):
    """Returns preset replies in order (the last one repeats) and records every prompt.

    A reply is a string, an AIMessage (e.g. with tool_calls), or a function of the
    messages returning either, so a script can use ids returned by earlier tool calls.
    """

    replies: list[Any]
    prompts: list[list[BaseMessage]] = []
    bound_tools: list[str] = []
    # Tool names bound for each call ([] when called without tools).
    tools_per_call: list[list[str]] = []

    @property
    def _llm_type(self) -> str:
        return "personaos-scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": [getattr(t, "name", str(t)) for t in tools]})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # model_copy() shares these lists, so bound copies record into the original.
        self.prompts.append(list(messages))
        self.tools_per_call.append(list(self.bound_tools))
        reply = self.replies[min(len(self.prompts), len(self.replies)) - 1]
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, AIMessage):
            # A fresh message per call, like a real model (a reused object keeps its id,
            # and LangGraph would treat a repeat as an update of the earlier message).
            n = len(self.prompts)
            message = reply.model_copy(
                update={
                    "id": None,
                    "tool_calls": [{**tc, "id": f"{tc['id']}_{n}"} for tc in reply.tool_calls],
                }
            )
        else:
            message = AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=message)])
