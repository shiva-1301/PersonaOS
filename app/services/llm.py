"""LLM and embedding factory. Providers are chosen by config only (LLM_PROVIDER,
EMBEDDING_PROVIDER); adding a provider means adding one small builder function.

Every chat call should go through `invoke_with_backoff`, which retries HTTP 429 rate
limits (and transient 5xx/connection errors) with exponential backoff, honouring the
server's suggested retry delay.
"""

import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import ModelAPIError, ModelConnectionError, ModelRateLimitError
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import Settings

logger = logging.getLogger(__name__)

# Chosen by scripts/smoke_tool_calling.py (see docs/DECISIONS.md D1a).
DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",
    "ollama": "qwen2.5:7b",
    "fake": "fake-chat",
}

MAX_BACKOFF_SECONDS = 60.0
# Worth retrying: rate limits, provider-side 5xx ("high demand"), dropped connections.
# Not retried: bad request, auth, permission, not found, timeouts (already slow).
RETRYABLE_ERRORS = (ModelRateLimitError, ModelAPIError, ModelConnectionError)


class LLMConfigError(RuntimeError):
    """The configured provider cannot be built (missing key, unknown provider...)."""


def chat_model_name(settings: Settings) -> str:
    return settings.LLM_MODEL or DEFAULT_MODELS[settings.LLM_PROVIDER]


# ------------------------------------------------------------------ chat models


def _gemini_chat(settings: Settings, model: str, json_mode: bool) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    if not settings.GEMINI_API_KEY:
        raise LLMConfigError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=settings.LLM_TEMPERATURE,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        # 1 = no SDK-level retries (0 would mean "SDK default"). 429s are retried by
        # invoke_with_backoff, which respects the server's retry delay; the SDK does not.
        max_retries=1,
        response_mime_type="application/json" if json_mode else None,
    )


def _ollama_chat(settings: Settings, model: str, json_mode: bool) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
        num_ctx=settings.OLLAMA_NUM_CTX,
        client_kwargs={"timeout": settings.LLM_TIMEOUT_SECONDS},
        format="json" if json_mode else None,
    )


def _fake_chat(settings: Settings, model: str, json_mode: bool) -> BaseChatModel:
    from app.services.fakes import FakeChatModel

    return FakeChatModel()


_CHAT_BUILDERS: dict[str, Callable[[Settings, str, bool], BaseChatModel]] = {
    "gemini": _gemini_chat,
    "ollama": _ollama_chat,
    "fake": _fake_chat,
}


def get_chat_model(
    settings: Settings, *, model: str | None = None, json_mode: bool = False
) -> BaseChatModel:
    """`json_mode` constrains output to valid JSON (Ollama format, Gemini MIME type)."""
    builder = _CHAT_BUILDERS.get(settings.LLM_PROVIDER)
    if builder is None:
        raise LLMConfigError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER}")
    return builder(settings, model or chat_model_name(settings), json_mode)


def memory_model_settings(settings: Settings) -> Settings:
    """Settings view for Mem0's extraction model (MEMORY_LLM_* override the chat LLM)."""
    provider = settings.MEMORY_LLM_PROVIDER or settings.LLM_PROVIDER
    if settings.MEMORY_LLM_MODEL:
        model = settings.MEMORY_LLM_MODEL
    elif provider == settings.LLM_PROVIDER:
        model = settings.LLM_MODEL
    else:
        model = None  # provider default
    return settings.model_copy(
        update={
            "LLM_PROVIDER": provider,
            "LLM_MODEL": model,
            "OLLAMA_NUM_CTX": settings.MEMORY_OLLAMA_NUM_CTX,
            "LLM_TIMEOUT_SECONDS": settings.MEMORY_LLM_TIMEOUT_SECONDS,
            # Extraction should be deterministic, not creative.
            "LLM_TEMPERATURE": 0.0,
        }
    )


def chat_model_settings(settings: Settings) -> Settings:
    """Settings view for the chat model.

    If chat and memory extraction use the SAME Ollama model, both must request the same
    context size: Ollama reloads a model whenever num_ctx changes, and alternating
    8k/16k requests made it evict and reload qwen on every turn.
    """
    mem = memory_model_settings(settings)
    if (
        settings.LLM_PROVIDER == "ollama"
        and mem.LLM_PROVIDER == "ollama"
        and chat_model_name(settings) == chat_model_name(mem)
    ):
        return settings.model_copy(
            update={"OLLAMA_NUM_CTX": max(settings.OLLAMA_NUM_CTX, mem.OLLAMA_NUM_CTX)}
        )
    return settings


def get_memory_model(settings: Settings) -> BaseChatModel:
    # Mem0's only LLM call here is fact extraction, which must return a JSON object.
    return get_chat_model(memory_model_settings(settings), json_mode=True)


# ------------------------------------------------------------------ embeddings


class TaskPrefixedEmbeddings(Embeddings):
    """nomic-embed-text is trained with task prefixes; queries and documents differ."""

    def __init__(self, inner: Embeddings, query_prefix: str, document_prefix: str):
        self.inner = inner
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self.query_prefix + text)


def _ollama_embedder(settings: Settings) -> Embeddings:
    from langchain_ollama import OllamaEmbeddings

    embedder = OllamaEmbeddings(
        model=settings.EMBEDDING_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        client_kwargs={"timeout": settings.LLM_TIMEOUT_SECONDS},
        # 0 GPU layers = CPU. Keeps VRAM for the chat model (see EMBEDDING_ON_CPU).
        num_gpu=0 if settings.EMBEDDING_ON_CPU else None,
    )
    if settings.EMBEDDING_MODEL.startswith("nomic-embed-text"):
        return TaskPrefixedEmbeddings(embedder, "search_query: ", "search_document: ")
    return embedder


def _gemini_embedder(settings: Settings) -> Embeddings:
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    if not settings.GEMINI_API_KEY:
        raise LLMConfigError("EMBEDDING_PROVIDER=gemini requires GEMINI_API_KEY")
    return GoogleGenerativeAIEmbeddings(
        model=settings.EMBEDDING_MODEL, google_api_key=settings.GEMINI_API_KEY
    )


def _fake_embedder(settings: Settings) -> Embeddings:
    from app.services.fakes import HashingEmbeddings

    return HashingEmbeddings()


_EMBED_BUILDERS: dict[str, Callable[[Settings], Embeddings]] = {
    "ollama": _ollama_embedder,
    "gemini": _gemini_embedder,
    "fake": _fake_embedder,
}


def get_embedder(settings: Settings) -> Embeddings:
    builder = _EMBED_BUILDERS.get(settings.EMBEDDING_PROVIDER)
    if builder is None:
        raise LLMConfigError(f"Unknown EMBEDDING_PROVIDER: {settings.EMBEDDING_PROVIDER}")
    return builder(settings)


def embedding_space_id(settings: Settings) -> str:
    """Stable id for the embedding model, used in Chroma collection names.

    Vectors from different models are incompatible (different dimensions/spaces), so a
    model change automatically lands in a new collection instead of corrupting one.
    """
    raw = f"{settings.EMBEDDING_PROVIDER}-{settings.EMBEDDING_MODEL}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._").lower()[:60]


# ------------------------------------------------------------------ rate limits

_RETRY_DELAY_PATTERNS = (
    re.compile(r"retry_delay\s*\{\s*seconds:\s*(\d+)"),  # protobuf text form
    re.compile(r"""['"]retryDelay['"]\s*:\s*['"](\d+(?:\.\d+)?)s['"]"""),  # JSON form
    re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE),  # human-readable form
)


def _server_retry_delay(exc: Exception) -> float | None:
    text = str(exc)
    for pattern in _RETRY_DELAY_PATTERNS:
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


def backoff_delay(attempt: int, exc: Exception) -> float:
    """Seconds to wait before retry number `attempt` (1-based)."""
    server = _server_retry_delay(exc)
    if server is not None:
        return min(server + 0.5, MAX_BACKOFF_SECONDS)
    return min(2.0**attempt + random.uniform(0, 1), MAX_BACKOFF_SECONDS)


def invoke_with_backoff(
    runnable: Runnable,
    model_input: Any,
    *,
    attempts: int,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Any:
    """`runnable.invoke(...)`, retrying only transient provider errors (RETRYABLE_ERRORS)."""
    for attempt in range(1, attempts + 1):
        try:
            return runnable.invoke(model_input, **kwargs)
        except RETRYABLE_ERRORS as exc:
            if attempt == attempts:
                raise
            delay = backoff_delay(attempt, exc)
            logger.warning(
                "LLM call failed transiently; retrying",
                extra={"error": type(exc).__name__, "attempt": attempt, "delay_s": round(delay, 1)},
            )
            sleep(delay)
    raise AssertionError("unreachable")
