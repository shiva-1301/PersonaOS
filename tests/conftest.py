import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests never read the developer's local .env.
    return Settings(_env_file=None, APP_ENV="test", AUTH_PROVIDER="fake", LOG_FORMAT="text")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
