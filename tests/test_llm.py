"""LLM factory, rate-limit backoff, fakes and Mem0 wiring (all offline)."""

import json
import math

import pytest
from langchain_core.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelInvalidRequestError,
    ModelRateLimitError,
)

from app.config import Settings
from app.services import llm
from app.services.fakes import FakeChatModel, HashingEmbeddings, fake_extract
from app.services.llm import (
    LLMConfigError,
    TaskPrefixedEmbeddings,
    backoff_delay,
    chat_model_name,
    chat_model_settings,
    embedding_space_id,
    get_chat_model,
    get_embedder,
    get_memory_model,
    invoke_with_backoff,
    memory_model_settings,
)
from app.services.memory_service import build_memory, mem0_collection_name
from app.services.vectorstore import get_chroma_client


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, APP_ENV="test", **kw)


# --------------------------------------------------------------------------- factory


def test_gemini_requires_key():
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        get_chat_model(_settings(LLM_PROVIDER="gemini"))


def test_gemini_model_built_without_sdk_retries():
    m = get_chat_model(_settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="k", LLM_MODEL="x-flash"))
    assert type(m).__name__ == "ChatGoogleGenerativeAI"
    assert m.model.endswith("x-flash")
    assert m.max_retries == 1


def test_ollama_model_uses_config():
    s = _settings(LLM_PROVIDER="ollama", OLLAMA_BASE_URL="http://h:1", OLLAMA_NUM_CTX=4096)
    m = get_chat_model(s)
    assert type(m).__name__ == "ChatOllama"
    assert (m.model, m.base_url, m.num_ctx) == ("qwen2.5:7b", "http://h:1", 4096)


def test_default_model_per_provider():
    assert chat_model_name(_settings(LLM_PROVIDER="ollama")) == "qwen2.5:7b"
    assert chat_model_name(_settings(LLM_PROVIDER="ollama", LLM_MODEL="llama3.1:8b")) == (
        "llama3.1:8b"
    )


def test_nomic_embedder_gets_task_prefixes():
    e = get_embedder(_settings(EMBEDDING_PROVIDER="ollama", EMBEDDING_MODEL="nomic-embed-text"))
    assert isinstance(e, TaskPrefixedEmbeddings)

    class Spy:
        def embed_documents(self, texts):
            return texts

        def embed_query(self, text):
            return text

    p = TaskPrefixedEmbeddings(Spy(), "q: ", "d: ")
    assert p.embed_query("x") == "q: x"
    assert p.embed_documents(["a", "b"]) == ["d: a", "d: b"]


def test_fake_providers_refused_in_production():
    with pytest.raises(ValueError, match="LLM_PROVIDER=fake"):
        Settings(_env_file=None, APP_ENV="production", LLM_PROVIDER="fake")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER=fake"):
        Settings(_env_file=None, APP_ENV="production", EMBEDDING_PROVIDER="fake")


def test_embedding_space_id_is_safe_and_model_specific():
    a = embedding_space_id(
        _settings(EMBEDDING_PROVIDER="ollama", EMBEDDING_MODEL="nomic-embed-text")
    )
    b = embedding_space_id(_settings(EMBEDDING_PROVIDER="ollama", EMBEDDING_MODEL="mxbai:latest"))
    assert a == "ollama-nomic-embed-text"
    assert a != b
    assert all(c.isalnum() or c in "._-" for c in b)


# --------------------------------------------------------------------------- backoff


class Flaky:
    def __init__(self, failures: int, exc=None):
        self.failures = failures
        self.calls = 0
        self.exc = exc or ModelRateLimitError("429 quota. retry_delay { seconds: 7 }")

    def invoke(self, x, **kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return "ok"


def test_backoff_retries_rate_limits_and_honours_server_delay():
    sleeps = []
    flaky = Flaky(failures=2)
    assert invoke_with_backoff(flaky, "x", attempts=4, sleep=sleeps.append) == "ok"
    assert flaky.calls == 3
    assert sleeps == [7.5, 7.5]


def test_backoff_gives_up_after_attempts():
    flaky = Flaky(failures=10)
    with pytest.raises(ModelRateLimitError):
        invoke_with_backoff(flaky, "x", attempts=3, sleep=lambda s: None)
    assert flaky.calls == 3


@pytest.mark.parametrize("exc", [ModelAPIError("503 high demand"), ModelConnectionError("reset")])
def test_backoff_retries_transient_server_errors(exc):
    flaky = Flaky(failures=1, exc=exc)
    assert invoke_with_backoff(flaky, "x", attempts=3, sleep=lambda s: None) == "ok"
    assert flaky.calls == 2


@pytest.mark.parametrize(
    "exc", [ValueError("bad"), ModelInvalidRequestError("400"), ModelAuthenticationError("401")]
)
def test_backoff_does_not_retry_permanent_errors(exc):
    flaky = Flaky(failures=1, exc=exc)
    with pytest.raises(type(exc)):
        invoke_with_backoff(flaky, "x", attempts=4, sleep=lambda s: None)
    assert flaky.calls == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("retry_delay { seconds: 12 }", 12.5),
        ('{"retryDelay": "30s"}', 30.5),
        ("Please retry in 4.2s.", 4.7),
        ("retry_delay { seconds: 500 }", llm.MAX_BACKOFF_SECONDS),
    ],
)
def test_server_delay_parsing(text, expected):
    assert backoff_delay(1, ModelRateLimitError(text)) == pytest.approx(expected)


def test_exponential_backoff_without_hint():
    assert 2 <= backoff_delay(1, ModelRateLimitError("429")) < 3
    assert 8 <= backoff_delay(3, ModelRateLimitError("429")) < 9


# --------------------------------------------------------------------------- fakes


def test_hashing_embeddings_similarity():
    e = HashingEmbeddings()
    a, b, c = e.embed_documents(["I study best after 6 pm", "study schedule", "pizza toppings"])

    def cos(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert math.isclose(cos(a, a), 1.0, rel_tol=1e-9)
    assert cos(a, b) > cos(a, c)


def test_fake_extract_takes_user_lines_only():
    prompt = (
        "## Existing Memories\n[]\n\n"
        "## New Messages\nuser: I like tea\nassistant: Noted\n\n"
        "## Observation Date\nx"
    )
    assert json.loads(fake_extract(prompt)) == {"memory": [{"text": "I like tea"}]}


# --------------------------------------------------------------------------- Mem0 wiring


def test_mem0_uses_our_models_and_never_openai(tmp_path, monkeypatch):
    """Trap from the handoff: Mem0 must not silently default to OpenAI."""
    import openai

    def forbidden(*a, **k):
        raise AssertionError("Mem0 tried to use OpenAI")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(openai.OpenAI, "__init__", forbidden)

    s = _settings(LLM_PROVIDER="fake", EMBEDDING_PROVIDER="fake", CHROMA_PATH=str(tmp_path))
    chroma = get_chroma_client(s.CHROMA_PATH)
    memory = build_memory(s, FakeChatModel(), HashingEmbeddings(), chroma)

    assert type(memory.llm).__name__ == "Mem0ChatAdapter"
    assert type(memory.embedding_model).__name__ == "Mem0EmbedderAdapter"
    assert (
        memory.collection_name == mem0_collection_name(s) == "personaos_mem0__fake-nomic-embed-text"
    )
    assert memory.config.history_db_path.startswith(str(tmp_path.resolve()))

    added = memory.add([{"role": "user", "content": "I like green tea"}], user_id="u1")
    assert [r["event"] for r in added["results"]] == ["ADD"]
    found = memory.search("tea", filters={"user_id": "u1"}, top_k=3)["results"]
    assert found[0]["memory"] == "I like green tea"
    assert memory.search("tea", filters={"user_id": "u2"}, top_k=3)["results"] == []


# --------------------------------------------------------------------------- memory model


def test_memory_model_defaults_to_chat_model():
    m = memory_model_settings(_settings(LLM_PROVIDER="ollama", LLM_MODEL="llama3.1:8b"))
    assert (m.LLM_PROVIDER, m.LLM_MODEL) == ("ollama", "llama3.1:8b")
    assert m.OLLAMA_NUM_CTX == 16384  # Mem0's ~8.5k-token prompt needs a bigger window
    assert m.LLM_TEMPERATURE == 0.0


def test_memory_model_can_differ_from_chat_model():
    s = _settings(LLM_PROVIDER="gemini", LLM_MODEL="gemini-x", MEMORY_LLM_PROVIDER="ollama")
    m = memory_model_settings(s)
    assert (m.LLM_PROVIDER, chat_model_name(m)) == ("ollama", "qwen2.5:7b")
    s2 = s.model_copy(update={"MEMORY_LLM_MODEL": "llama3.1:8b"})
    assert chat_model_name(memory_model_settings(s2)) == "llama3.1:8b"
    mm = get_memory_model(s)
    assert type(mm).__name__ == "ChatOllama"
    assert mm.format == "json"  # extraction output is constrained to valid JSON
    assert get_chat_model(s.model_copy(update={"LLM_PROVIDER": "ollama"})).format is None
    gm = get_memory_model(
        s.model_copy(update={"MEMORY_LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k"})
    )
    assert gm.response_mime_type == "application/json"


def test_memory_ctx_must_fit_mem0_prompt():
    with pytest.raises(ValueError):
        _settings(MEMORY_OLLAMA_NUM_CTX=8192)


# --------------------------------------------------------------------------- Ollama VRAM


def test_same_ollama_model_shares_one_context_size():
    """Different num_ctx for chat vs extraction made Ollama reload qwen every turn."""
    s = _settings(LLM_PROVIDER="ollama", MEMORY_LLM_PROVIDER="ollama")
    assert chat_model_settings(s).OLLAMA_NUM_CTX == memory_model_settings(s).OLLAMA_NUM_CTX
    assert get_chat_model(chat_model_settings(s)).num_ctx == get_memory_model(s).num_ctx


def test_different_models_keep_their_own_context():
    s = _settings(
        LLM_PROVIDER="ollama",
        LLM_MODEL="llama3.1:8b",
        MEMORY_LLM_PROVIDER="ollama",
        MEMORY_LLM_MODEL="qwen2.5:7b",
    )
    assert chat_model_settings(s).OLLAMA_NUM_CTX == s.OLLAMA_NUM_CTX == 8192
    g = _settings(LLM_PROVIDER="gemini", MEMORY_LLM_PROVIDER="ollama")
    assert chat_model_settings(g) is g


def test_ollama_embeddings_run_on_cpu_by_default():
    e = get_embedder(_settings(EMBEDDING_PROVIDER="ollama"))
    assert e.inner.num_gpu == 0
    e2 = get_embedder(_settings(EMBEDDING_PROVIDER="ollama", EMBEDDING_ON_CPU=False))
    assert e2.inner.num_gpu is None
