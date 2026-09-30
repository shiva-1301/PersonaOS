from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_starts_without_optional_integrations():
    s = Settings(_env_file=None, APP_ENV="test")
    assert s.GOOGLE_CLIENT_ID is None
    assert s.CRON_SECRET is None
    assert s.google_enabled is False
    create_app(s)  # must not raise


def test_fake_auth_forbidden_in_production():
    with pytest.raises(ValidationError, match="AUTH_PROVIDER=fake"):
        Settings(_env_file=None, APP_ENV="production", AUTH_PROVIDER="fake")


def test_secrets_are_masked():
    s = Settings(_env_file=None, GEMINI_API_KEY="super-secret-value")
    assert "super-secret-value" not in repr(s)
    assert s.GEMINI_API_KEY.get_secret_value() == "super-secret-value"


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("MAX_UPLOAD_MB", "25")
    s = Settings(_env_file=None)
    assert s.LLM_PROVIDER == "ollama"
    assert s.MAX_UPLOAD_MB == 25


def test_blank_env_values_treated_as_unset(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("CHROMA_PATH", "")
    s = Settings(_env_file=None)
    assert s.GEMINI_API_KEY is None
    assert s.CHROMA_PATH == "./chroma_data"


def test_env_example_parses():
    # The committed template must always load cleanly (blank secrets are fine).
    s = Settings(_env_file=Path(__file__).parent.parent / ".env.example")
    assert s.EMBEDDING_PROVIDER == "ollama"
    assert s.GEMINI_API_KEY is None


def test_invalid_provider_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_PROVIDER="not-a-provider")


def test_docs_hidden_in_production():
    s = Settings(_env_file=None, APP_ENV="production", AUTH_PROVIDER="firebase")
    assert TestClient(create_app(s)).get("/docs").status_code == 404
