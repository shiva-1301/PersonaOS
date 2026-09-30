"""Application settings loaded from environment variables (and `.env` in local dev)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["dev", "test", "production"]


def build_postgres_url(user: str, password: str | None, host: str, port: int, db: str) -> str:
    auth = quote(user, safe="") + (f":{quote(password, safe='')}" if password else "")
    return f"postgresql+psycopg://{auth}@{host}:{port}/{db}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Blank entries in .env (e.g. `GEMINI_API_KEY=`) count as unset, not "".
        env_ignore_empty=True,
    )

    # --- App ---
    APP_ENV: AppEnv = "dev"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"

    # --- Database ---
    # If DATABASE_URL is blank it is built from the POSTGRES_* values (host-side runs).
    DATABASE_URL: str | None = None
    POSTGRES_USER: str = "personaos"
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_DB: str = "personaos"
    POSTGRES_HOST: str = "127.0.0.1"  # not "localhost": on Windows that tries IPv6 first and hangs
    POSTGRES_HOST_PORT: int = 5432

    # --- LLM / embeddings (swappable by config only) ---
    # `fake` = deterministic offline models for tests/CI (refused in production).
    LLM_PROVIDER: Literal["gemini", "ollama", "fake"] = "gemini"
    # Blank -> provider default (see app/services/llm.py DEFAULT_MODELS).
    LLM_MODEL: str | None = None
    LLM_TEMPERATURE: float = Field(default=0.3, ge=0, le=2)
    LLM_TIMEOUT_SECONDS: float = Field(default=60, gt=0)
    # Attempts on HTTP 429 (exponential backoff, honours the server's retry delay).
    LLM_RATE_LIMIT_ATTEMPTS: int = Field(default=4, ge=1, le=10)
    EMBEDDING_PROVIDER: Literal["ollama", "gemini", "fake"] = "ollama"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    # Run Ollama embeddings on CPU. The embedding model is tiny (fast on CPU), and keeping
    # it off the GPU stops Ollama evicting/reloading the chat model on small GPUs.
    EMBEDDING_ON_CPU: bool = True
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    # Context window for Ollama chat models (memories + history need more than the default).
    OLLAMA_NUM_CTX: int = Field(default=8192, ge=2048)
    GEMINI_API_KEY: SecretStr | None = None

    # Model Mem0 uses to extract memories (runs in the background after each reply).
    # Blank -> same provider/model as the chat LLM. Its prompt is ~8.5k tokens, so it
    # gets its own (larger) Ollama context window and a longer timeout.
    MEMORY_LLM_PROVIDER: Literal["gemini", "ollama", "fake"] | None = None
    MEMORY_LLM_MODEL: str | None = None
    MEMORY_OLLAMA_NUM_CTX: int = Field(default=16384, ge=12288)
    MEMORY_LLM_TIMEOUT_SECONDS: float = Field(default=180, gt=0)

    # --- Chat / memory ---
    # Previous messages of the session sent to the LLM each turn.
    CHAT_HISTORY_LIMIT: int = Field(default=20, ge=0, le=200)
    MEMORY_RECALL_K: int = Field(default=5, ge=1, le=50)
    MAX_MESSAGE_CHARS: int = Field(default=8000, ge=100)

    # --- Auth ---
    AUTH_PROVIDER: Literal["firebase", "fake"] = "firebase"
    FIREBASE_PROJECT_ID: str | None = None

    # --- Storage ---
    CHROMA_PATH: str = "./chroma_data"
    MAX_UPLOAD_MB: int = Field(default=10, gt=0)

    # --- Web ---
    FRONTEND_ORIGIN: str = "http://localhost:8501"

    # --- Optional integrations (absence must never crash startup) ---
    CRON_SECRET: SecretStr | None = None
    TOKEN_ENCRYPTION_KEY: SecretStr | None = None
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: SecretStr | None = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/integrations/google/callback"

    @model_validator(mode="after")
    def _forbid_fake_auth_in_production(self) -> "Settings":
        if self.APP_ENV == "production":
            for name in (
                "AUTH_PROVIDER",
                "LLM_PROVIDER",
                "MEMORY_LLM_PROVIDER",
                "EMBEDDING_PROVIDER",
            ):
                if getattr(self, name) == "fake":
                    raise ValueError(f"{name}=fake is not allowed when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _default_database_url(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = build_postgres_url(
                self.POSTGRES_USER,
                self.POSTGRES_PASSWORD.get_secret_value() if self.POSTGRES_PASSWORD else None,
                self.POSTGRES_HOST,
                self.POSTGRES_HOST_PORT,
                self.POSTGRES_DB,
            )
        return self

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)


@lru_cache
def get_settings() -> Settings:
    return Settings()
