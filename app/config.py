"""Application settings loaded from environment variables (and `.env` in local dev)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["dev", "test", "production"]


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
    DATABASE_URL: str = "postgresql+psycopg://personaos@localhost:5432/personaos"

    # --- LLM / embeddings (swappable by config only) ---
    LLM_PROVIDER: Literal["gemini", "ollama", "openai", "fake"] = "gemini"
    LLM_MODEL: str | None = None
    EMBEDDING_PROVIDER: Literal["ollama", "gemini", "openai", "fake"] = "ollama"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    GEMINI_API_KEY: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_BASE_URL: str | None = None

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
        if self.APP_ENV == "production" and self.AUTH_PROVIDER == "fake":
            raise ValueError("AUTH_PROVIDER=fake is not allowed when APP_ENV=production")
        return self

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)


@lru_cache
def get_settings() -> Settings:
    return Settings()
