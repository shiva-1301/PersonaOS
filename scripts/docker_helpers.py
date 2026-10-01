"""Shared helpers for the scripts that verify the RUNNING Docker stack."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:8000"
BASE_COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
FAKE_COMPOSE = [*BASE_COMPOSE, "-f", str(ROOT / "docker-compose.fake-auth.yml")]


def http(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    return _send(req)


def upload(path: Path, token: str):
    """multipart/form-data upload with the standard library only."""
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        API + "/documents",
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    return _send(req)


def _send(req):
    """(status, body): body is parsed JSON, the raw text if it isn't JSON (e.g. an
    HTML page), or None when empty."""
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def _decode(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", errors="replace")


def wait_healthy(timeout: float = 180) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if http("GET", "/health")[0] == 200:
                return time.monotonic() - start
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    raise SystemExit(f"API not healthy after {timeout}s: docker compose logs api")


def compose(args: list[str], base: list[str]) -> None:
    subprocess.run([*base, *args], cwd=ROOT, check=True, capture_output=True)


def container_python(code: str, base: list[str]) -> str:
    out = subprocess.run(
        [*base, "exec", "-T", "api", "python", "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


CONTAINER_PRELUDE = """
import json, logging, uuid
logging.disable(logging.WARNING)
from app.config import Settings
from app.db.session import make_engine, make_session_factory
from app.services.container import Services
s = Settings()
svc = Services(s)
db = make_session_factory(make_engine(s.DATABASE_URL))()
"""


def container_json(code: str, base: list[str]):
    """Run `code` (which must print one JSON line last) inside the api container."""
    return json.loads(container_python(CONTAINER_PRELUDE + code, base).splitlines()[-1])


def cleanup_in_container(auth_uids: list[str], base: list[str]) -> None:
    """Delete verification users everywhere: Postgres rows, Mem0 vectors + SQLite
    history, and document chunks (a Postgres cascade does not reach Chroma)."""
    code = f"""
import sqlite3
from sqlalchemy import select
from app.db.models import MemoryMeta, User
hist = sqlite3.connect(s.CHROMA_PATH + "/mem0_history.db")
for uid in {auth_uids!r}:
    user = db.scalar(select(User).where(User.auth_uid == uid))
    if not user:
        continue
    ids = list(db.scalars(select(MemoryMeta.mem0_id).where(MemoryMeta.user_id == user.id)))
    svc.memory._memory.delete_all(user_id=str(user.id))
    hist.executemany("delete from history where memory_id = ?", [(i,) for i in ids])
    hist.execute("delete from messages where session_scope = ?", (f"user_id={{user.id}}",))
    svc.rag.collection.delete(where={{"user_id": str(user.id)}})
    db.delete(user)
hist.commit()
db.commit()
print(json.dumps("cleaned"))
"""
    container_json(code, base)


def firebase_token(email: str, password: str) -> str:
    from dotenv import dotenv_values

    key = {**dotenv_values(ROOT / ".env"), **os.environ}.get("FIREBASE_WEB_API_KEY")
    if not key:
        raise SystemExit("FIREBASE_WEB_API_KEY missing in .env")
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["idToken"]


class Checks:
    def __init__(self):
        self.failures: list[str] = []

    def __call__(self, ok: bool, label: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            self.failures.append(label)

    def result(self) -> int:
        n = len(self.failures)
        print("\nRESULT:", "PASS" if not n else f"FAIL ({n}): {self.failures}")
        return 0 if not n else 1


def stream_chat(token: str, message: str) -> list[tuple[float, str, dict]]:
    """POST /chat/stream; returns [(seconds_since_start, event, data), ...]."""
    started = time.monotonic()
    req = urllib.request.Request(
        API + "/chat/stream",
        method="POST",
        data=json.dumps({"message": message}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    events, event = [], None
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append(
                    (time.monotonic() - started, event, json.loads(line[len("data: ") :]))
                )
    return events
