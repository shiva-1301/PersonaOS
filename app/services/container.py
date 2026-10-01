"""Lazily-built heavy services (models, Chroma, Mem0), one set per app instance.

Lazy so the API starts (and /health answers) even when a provider is misconfigured or
Ollama is down; the failure surfaces on the first request that needs it instead.
Tests replace attributes directly (e.g. `services.chat_model = FakeChatModel()`).
"""

import threading
from typing import Any

from app.config import Settings
from app.services.llm import (
    chat_model_settings,
    get_chat_model,
    get_embedder,
    get_memory_model,
)
from app.services.memory_service import MemoryService, build_memory
from app.services.rag_service import RagService
from app.services.vectorstore import get_chroma_client


class Services:
    def __init__(self, settings: Settings, session_factory=None):
        self.settings = settings
        # DB sessions for work outside the request's own session (agent tools).
        self.session_factory = session_factory
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}

    def _get(self, name: str, factory):
        with self._lock:
            if name not in self._cache:
                self._cache[name] = factory()
            return self._cache[name]

    def __setattr__(self, name: str, value: Any) -> None:
        if name in (
            "chat_model",
            "planner_model",
            "memory_model",
            "embedder",
            "chroma",
            "memory",
            "rag",
            "agent_graph",
        ):
            self._cache[name] = value
        else:
            super().__setattr__(name, value)

    @property
    def chat_model(self):
        return self._get("chat_model", lambda: get_chat_model(chat_model_settings(self.settings)))

    @property
    def planner_model(self):
        """The chat model in JSON mode, for structured outputs such as study plans."""
        return self._get(
            "planner_model",
            lambda: get_chat_model(chat_model_settings(self.settings), json_mode=True),
        )

    @property
    def memory_model(self):
        """Model Mem0 uses for extraction (MEMORY_LLM_* or the chat model's settings)."""
        return self._get("memory_model", lambda: get_memory_model(self.settings))

    @property
    def embedder(self):
        return self._get("embedder", lambda: get_embedder(self.settings))

    @property
    def chroma(self):
        return self._get("chroma", lambda: get_chroma_client(self.settings.CHROMA_PATH))

    @property
    def memory(self) -> MemoryService:
        return self._get(
            "memory",
            lambda: MemoryService(
                build_memory(self.settings, self.memory_model, self.embedder, self.chroma),
                self.settings,
                checker=self.memory_model,
                session_factory=self.session_factory,
            ),
        )

    @property
    def rag(self) -> RagService:
        return self._get("rag", lambda: RagService(self.settings, self.embedder, self.chroma))

    @property
    def agent_graph(self):
        """The compiled LangGraph agent (built once; per-turn context comes via config)."""
        from app.agent.graph import build_agent_graph  # late import: graph imports services

        return self._get("agent_graph", build_agent_graph)
