"""Frontend settings from the environment (Docker) or the project's .env (local runs)."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class FrontendSettings:
    api_url: str
    firebase_api_key: str
    max_upload_mb: int


def load_settings() -> FrontendSettings:
    values: dict[str, str | None] = {}
    try:  # local runs read .env; in Docker compose passes the variables in
        from dotenv import dotenv_values

        values = dotenv_values(ROOT / ".env")
    except ImportError:
        pass

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name) or values.get(name) or default

    return FrontendSettings(
        api_url=get("API_URL", "http://127.0.0.1:8000"),
        firebase_api_key=get("FIREBASE_WEB_API_KEY"),
        max_upload_mb=int(get("MAX_UPLOAD_MB", "10")),
    )
