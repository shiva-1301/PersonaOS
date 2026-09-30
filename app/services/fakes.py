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
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.prompts import MEMORIES_HEADER

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


class FakeChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "personaos-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system = "\n".join(_content(m) for m in messages if m.type == "system")
        if EXTRACTION_MARKER in system:
            text = fake_extract("\n".join(_content(m) for m in messages if m.type == "human"))
        else:
            text = fake_reply(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
