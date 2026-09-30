from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.auth.jwt_verify import FirebaseVerifier, build_verifier
from app.config import Settings
from app.main import create_app


def test_starts_without_optional_integrations():
    s = Settings(_env_file=None, APP_ENV="test", FIREBASE_PROJECT_ID="demo-project")
    assert s.GOOGLE_CLIENT_ID is None
    assert s.CRON_SECRET is None
    assert s.GEMINI_API_KEY is None
    assert s.google_enabled is False
    app = create_app(s)  # must not raise, and must not need a reachable DB
    assert isinstance(app.state.verifier, FirebaseVerifier)


def test_firebase_auth_requires_project_id():
    with pytest.raises(RuntimeError, match="FIREBASE_PROJECT_ID"):
        create_app(Settings(_env_file=None, APP_ENV="test", AUTH_PROVIDER="firebase"))


def test_fake_auth_forbidden_in_production():
    with pytest.raises(ValidationError, match="AUTH_PROVIDER=fake"):
        Settings(_env_file=None, APP_ENV="production", AUTH_PROVIDER="fake")


def test_build_verifier_refuses_fake_outside_dev_test():
    s = Settings(_env_file=None, APP_ENV="test", AUTH_PROVIDER="fake")
    object.__setattr__(s, "APP_ENV", "production")  # bypass validation deliberately
    with pytest.raises(RuntimeError, match="Fake auth"):
        build_verifier(s)


def test_database_url_built_and_escaped():
    s = Settings(
        _env_file=None,
        POSTGRES_USER="me",
        POSTGRES_PASSWORD="p@ss/w:rd% x",
        POSTGRES_DB="db1",
        POSTGRES_HOST_PORT=5433,
    )
    assert s.DATABASE_URL == "postgresql+psycopg://me:p%40ss%2Fw%3Ard%25%20x@127.0.0.1:5433/db1"
    assert make_url(s.DATABASE_URL).password == "p@ss/w:rd% x"


def test_explicit_database_url_wins():
    s = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://x@db:5432/y")
    assert s.DATABASE_URL == "postgresql+psycopg://x@db:5432/y"


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
    s = Settings(
        _env_file=None,
        APP_ENV="production",
        AUTH_PROVIDER="firebase",
        FIREBASE_PROJECT_ID="demo-project",
    )
    assert TestClient(create_app(s)).get("/docs").status_code == 404
