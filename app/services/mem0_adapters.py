"""Adapters that let Mem0 use the exact LangChain models built by `app.services.llm`.

Mem0 only accepts known provider names, so `build_memory` registers these classes under
Mem0's "langchain" provider name. This guarantees Mem0 never falls back to a default
provider (e.g. OpenAI) and shares rate-limit handling with the rest of the app.
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase

from app.services.llm import invoke_with_backoff

_ROLE_TO_MESSAGE = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


class Mem0ChatAdapter(LLMBase):
    """`config.model` is a LangChain chat model; `config.rate_limit_attempts` optional."""

    def _validate_config(self) -> None:
        if self.config.model is None or not hasattr(self.config.model, "invoke"):
            raise ValueError("Mem0ChatAdapter needs a LangChain chat model as `model`")

    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: Any = None,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> str:
        if tools:
            raise NotImplementedError("PersonaOS does not use Mem0 graph/tool features")
        lc_messages = [
            _ROLE_TO_MESSAGE[m["role"]](content=m["content"])
            for m in messages
            if m.get("role") in _ROLE_TO_MESSAGE and m.get("content") is not None
        ]
        attempts = getattr(self.config, "rate_limit_attempts", None) or 4
        result = invoke_with_backoff(self.config.model, lc_messages, attempts=attempts)
        content = result.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return content


class Mem0EmbedderAdapter(EmbeddingBase):
    """`config.model` is a LangChain Embeddings instance."""

    def embed(self, text: str, memory_action: Literal["add", "search", "update"] | None = None):
        model = self.config.model
        if memory_action == "search":
            return model.embed_query(text)
        return model.embed_documents([text])[0]

    def embed_batch(self, texts: list[str], memory_action: str = "add"):
        if memory_action == "search":
            return [self.config.model.embed_query(t) for t in texts]
        return self.config.model.embed_documents(list(texts))
