"""Live smoke tests against the REAL providers configured in .env (Gemini + Ollama).

Excluded by default (`-m 'not live'`). Run explicitly:
    .\\scripts\\test.ps1 -m live -s
Uses fake data only, a temporary Chroma directory and the personaos_test database.
"""

import re
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import ROOT, auth
from tests.test_memory_regression import wait_for_memory

pytestmark = pytest.mark.live


@pytest.fixture
def live_client(db_url, tmp_path):
    # Providers/models come from the process environment first, then .env, so a run can
    # be pointed elsewhere without editing .env, e.g. $env:LLM_PROVIDER="ollama".
    settings = Settings(
        _env_file=ROOT / ".env",
        APP_ENV="test",
        AUTH_PROVIDER="fake",
        LOG_FORMAT="text",
        DATABASE_URL=db_url,
        CHROMA_PATH=str(tmp_path / "chroma"),
    )
    assert "fake" not in (settings.LLM_PROVIDER, settings.EMBEDDING_PROVIDER), "live = real"
    print(
        f"\nlive: chat={settings.LLM_PROVIDER}:{settings.LLM_MODEL or 'default'} "
        f"memory={settings.MEMORY_LLM_PROVIDER or 'same'}:{settings.MEMORY_LLM_MODEL or 'default'} "
        f"embed={settings.EMBEDDING_PROVIDER}:{settings.EMBEDDING_MODEL}"
    )
    app = create_app(settings)
    yield TestClient(app)
    from sqlalchemy import text

    from app.db.models import Base

    with app.state.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(t.name for t in Base.metadata.sorted_tables)}"))
    app.state.engine.dispose()


def test_remembers_preference_across_sessions(live_client):
    a, b = auth("test-live-a"), auth("test-live-b")

    first = live_client.post(
        "/chat", json={"message": "I'm bad at mornings. I study best after 6 pm."}, headers=a
    )
    assert first.status_code == 200, first.text
    started = time.monotonic()
    wait_for_memory(
        live_client, a, first.json()["session_id"], first.json()["message_id"], timeout=300
    )
    print(f"extraction done {time.monotonic() - started:.1f}s after the reply")

    second = live_client.post(
        "/chat", json={"message": "When should I schedule my study time?"}, headers=a
    ).json()
    print("\nA (new session):", second)
    assert second["session_id"] != first.json()["session_id"]
    assert second["memories_used"] >= 1
    assert re.search(r"\b(6|six|18:00|evening|after 6)", second["reply"], re.IGNORECASE)

    other = live_client.post(
        "/chat", json={"message": "When should I schedule my study time?"}, headers=b
    ).json()
    print("B:", other)
    assert other["memories_used"] == 0


def test_study_plan_with_real_model(live_client):
    """Real LLM: remembered evening preference -> dated plan within window and budget."""
    from datetime import UTC, datetime, timedelta

    a = auth("test-live-a")
    live_client.patch("/me", json={"timezone": "Asia/Kolkata"}, headers=a)
    first = live_client.post(
        "/chat", json={"message": "I'm bad at mornings. I study best after 6 pm."}, headers=a
    ).json()
    wait_for_memory(live_client, a, first["session_id"], first["message_id"], timeout=300)

    target = (datetime.now(UTC) + timedelta(days=27)).date()
    goal = live_client.post(
        "/goals",
        json={"title": "Finish the Machine Learning course", "target_date": target.isoformat()},
        headers=a,
    ).json()
    started = time.monotonic()
    resp = live_client.post(f"/goals/{goal['id']}/plan", json={"hours_per_week": 6}, headers=a)
    print(f"\nplan took {time.monotonic() - started:.1f}s -> {resp.status_code}")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    print("preferences:", body["used_preferences"])
    print("adjustments:", body["adjustments"])
    for t in body["tasks"]:
        print(f"  {t['due_at']}  {t['est_minutes']:>3} min  {t['title']}")
    assert len(body["tasks"]) >= 3
    assert any("6 pm" in p or "18" in p or "evening" in p for p in body["used_preferences"])
