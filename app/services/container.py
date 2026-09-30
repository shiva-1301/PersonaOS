"""Lazily-built heavy services (models, Chroma, Mem0), one set per app instance.

Lazy so the API starts (and /health answers) even when a provider is misconfigured or
Ollama is down; the failure surfaces on the first request that needs it instead.
Tests replace attributes directly (e.g. `services.chat_model = FakeChatModel()`).
"""

import threading
from typing import Any

from app.config import Settings
from app.services.llm import get_chat_model, get_embedder, get_memory_model
from app.services.memory_service import MemoryService, build_memory
from app.services.vectorstore import get_chroma_client


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}

    def _get(self, name: str, factory):
        with self._lock:
            if name not in self._cache:
                self._cache[name] = factory()
            return self._cache[name]

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("chat_model", "memory_model", "embedder", "chroma", "memory"):
            self._cache[name] = value
        else:
            super().__setattr__(name, value)

    @property
    def chat_model(self):
        return self._get("chat_model", lambda: get_chat_model(self.settings))

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
                build_memory(self.settings, self.memory_model, self.embedder, self.chroma)
            ),
        )
