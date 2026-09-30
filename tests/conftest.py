"""Shared fixtures.

DB tests use a dedicated Postgres database (default: `personaos_test` on the compose
Postgres, credentials from `.env`), migrated with Alembic once per session and truncated
after every test. If Postgres is unreachable, DB tests are skipped, unless
PERSONAOS_REQUIRE_DB=1 (set in CI), in which case they fail.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings, build_postgres_url
from app.db.models import Base
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent

# Never phone home from tests (read by Mem0 / Chroma at import time).
os.environ.setdefault("MEM0_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# Offline providers: CI and tests never call a real LLM or embedding service.
OFFLINE = {"LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "fake", "LLM_RATE_LIMIT_ATTEMPTS": 1}


@pytest.fixture
def settings(tmp_path) -> Settings:
    # _env_file=None: the app under test never reads the developer's local .env.
    return Settings(
        _env_file=None,
        APP_ENV="test",
        AUTH_PROVIDER="fake",
        LOG_FORMAT="text",
        CHROMA_PATH=str(tmp_path / "chroma"),
        **OFFLINE,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# ---------------------------------------------------------------- database


def _base_test_url(db_name: str) -> str:
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    if env.get("TEST_DATABASE_URL"):
        return str(make_url(env["TEST_DATABASE_URL"]).set(database=db_name))
    return build_postgres_url(
        env.get("POSTGRES_USER") or "personaos",
        env.get("POSTGRES_PASSWORD") or None,
        env.get("POSTGRES_HOST") or "127.0.0.1",
        int(env.get("POSTGRES_HOST_PORT") or 5432),
        db_name,
    )


def ensure_database(db_name: str) -> str:
    """Create `db_name` if missing and return its SQLAlchemy URL; skip/fail if no Postgres."""
    url = _base_test_url(db_name)
    # Pass parts as keyword args (not a URL string) so any password characters survive.
    args = (
        make_url(url)
        .set(database="postgres")
        .translate_connect_args(username="user", database="dbname")
    )
    try:
        with psycopg.connect(**args, autocommit=True, connect_timeout=5) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
            ).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    except psycopg.OperationalError as exc:
        msg = (
            f"Postgres not reachable for tests ({type(exc).__name__}). "
            "Start it: docker compose up -d postgres"
        )
        if os.environ.get("PERSONAOS_REQUIRE_DB") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    return url


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app" / "db" / "migrations"))
    # ConfigParser interpolates '%', and URL-escaped passwords may contain it.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    url = ensure_database("personaos_test")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def db_settings(db_url: str, tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV="test",
        AUTH_PROVIDER="fake",
        LOG_FORMAT="text",
        DATABASE_URL=db_url,
        CHROMA_PATH=str(tmp_path / "chroma"),
        **OFFLINE,
    )


@pytest.fixture
def db_app(db_settings: Settings) -> Iterator:
    app = create_app(db_settings)
    yield app
    # Clean slate for the next test: wipe every table (users cascade to all user data).
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with app.state.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    app.state.engine.dispose()


@pytest.fixture
def db_client(db_app) -> TestClient:
    return TestClient(db_app)


@pytest.fixture
def db_session(db_app):
    with db_app.state.session_factory() as session:
        yield session


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fresh_engine_factory():
    """Engines created by a test are disposed afterwards."""
    engines = []

    def make(url: str):
        engine = create_engine(url)
        engines.append(engine)
        return engine

    yield make
    for e in engines:
        e.dispose()
