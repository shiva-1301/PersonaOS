"""End-to-end memory check against the RUNNING Docker stack (real models, real restart).

  1. User A states a unique (random, fake) fact.
  2. Wait (polling memory_status, with a timeout) until extraction is done; report time.
  3. A new session asks about it: memories_used >= 1 and the recalled memory contains the
     fact (checked inside the container, so it is the real retrieval, not the LLM's words).
  4. `docker compose restart api`, wait for /health.
  5. A asks again in another new session: same checks.
  6. User B asks the same question: memories_used == 0 and B's recall has no trace of it.

Auth modes:
  --auth fake      (default) restarts the api with docker-compose.fake-auth.yml, uses
                   tokens test-verify-a / test-verify-b, deletes their data afterwards and
                   restores the normal (Firebase) api.
  --auth firebase  uses your Firebase test users; passwords from PERSONAOS_PASSWORD_A /
                   PERSONAOS_PASSWORD_B or a hidden prompt. Their data is left in place.

PowerShell:
  .venv\\Scripts\\python.exe scripts\\verify_memory_docker.py
  .venv\\Scripts\\python.exe scripts\\verify_memory_docker.py --auth firebase `
      --email-a a@example.com --email-b b@example.com
"""

import argparse
import getpass
import os
import random
import sys
import time

from docker_helpers import (
    BASE_COMPOSE,
    FAKE_COMPOSE,
    Checks,
    cleanup_in_container,
    compose,
    container_json,
    container_python,
    firebase_token,
    http,
    wait_healthy,
)

check = Checks()


def recall_in_container(user_id: str, query: str, base: list[str]) -> list[str]:
    """Run the app's real recall inside the api container; returns memory texts."""
    code = f"""
hits = svc.memory.recall(db, uuid.UUID({user_id!r}), {query!r}, k=s.MEMORY_RECALL_K)
print(json.dumps([h.text for h in hits]))
"""
    return container_json(code, base)


def wait_for_extraction(token: str, session_id: str, message_id: str, timeout: float) -> float:
    start = time.monotonic()
    while True:
        _, detail = http("GET", f"/chat/sessions/{session_id}", token)
        state = next(
            (m["memory_status"] for m in detail.get("messages", []) if m["id"] == message_id),
            None,
        )
        if state == "done":
            return time.monotonic() - start
        if state == "failed":
            raise SystemExit("memory extraction FAILED - see: docker compose logs api")
        if time.monotonic() - start > timeout:
            raise SystemExit(f"extraction still {state!r} after {timeout}s")
        time.sleep(1)


def ask(token: str, question: str) -> dict:
    status, body = http("POST", "/chat", token, {"message": question})
    if status != 200:
        raise SystemExit(f"/chat returned {status}: {body}")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify memory persistence in Docker")
    parser.add_argument("--auth", choices=["fake", "firebase"], default="fake")
    parser.add_argument("--email-a")
    parser.add_argument("--email-b")
    parser.add_argument("--extraction-timeout", type=float, default=300)
    args = parser.parse_args()

    base = FAKE_COMPOSE if args.auth == "fake" else BASE_COMPOSE
    token_code = random.randint(1000, 9999)
    fact = f"My study buddy's nickname is Zorblax{token_code}."
    question = "What is my study buddy's nickname?"
    marker = f"Zorblax{token_code}"

    if args.auth == "fake":
        print("Starting api with fake auth (docker-compose.fake-auth.yml)...")
        compose(["up", "-d", "api"], base)
        tok_a, tok_b = "test-verify-a", "test-verify-b"
        wait_healthy()
        # Start clean in case an earlier run was interrupted before its cleanup.
        cleanup_in_container([tok_a, tok_b], base)
    else:
        if not (args.email_a and args.email_b):
            raise SystemExit("--auth firebase needs --email-a and --email-b")
        pw_a = os.environ.get("PERSONAOS_PASSWORD_A") or getpass.getpass("Password A: ")
        pw_b = os.environ.get("PERSONAOS_PASSWORD_B") or getpass.getpass("Password B: ")
        tok_a, tok_b = firebase_token(args.email_a, pw_a), firebase_token(args.email_b, pw_b)
    wait_healthy()

    ollama_url = container_python(
        "from app.config import Settings; print(Settings().OLLAMA_BASE_URL)", base
    ).splitlines()[-1]
    print(f"api container OLLAMA_BASE_URL = {ollama_url}")
    check("host.docker.internal" in ollama_url, "container reaches Ollama via host.docker.internal")

    a_id = http("GET", "/me", tok_a)[1]["id"]
    b_id = http("GET", "/me", tok_b)[1]["id"]
    print(f"fact for this run: {fact!r}")

    try:
        print("\n1-2. User A states the fact; waiting for background extraction")
        t0 = time.monotonic()
        first = ask(tok_a, fact)
        reply_s = time.monotonic() - t0
        extract_s = wait_for_extraction(
            tok_a, first["session_id"], first["message_id"], args.extraction_timeout
        )
        print(f"  reply took {reply_s:.1f}s; extraction finished {extract_s:.1f}s after reply")

        print("\n3. User A, new session (before restart)")
        before = ask(tok_a, question)
        print(f"  memories_used={before['memories_used']} reply={before['reply'][:140]!r}")
        check(before["session_id"] != first["session_id"], "new session")
        check(before["memories_used"] >= 1, "memories injected into the prompt")
        # The codename is random and this session has no history: the model can only
        # know it if the retrieved memory was injected into its context.
        check(marker in before["reply"], f"reply uses the injected memory ({marker})")
        recalled = recall_in_container(a_id, question, base)
        check(any(marker in m for m in recalled), f"recall returns the {marker} memory")

        print("\n4. docker compose restart api")
        compose(["restart", "api"], base)
        print(f"  healthy again after {wait_healthy():.1f}s")

        print("\n5. User A, new session (after restart)")
        after = ask(tok_a, question)
        print(f"  memories_used={after['memories_used']} reply={after['reply'][:140]!r}")
        check(after["memories_used"] >= 1, "memories injected after restart")
        check(marker in after["reply"], f"reply after restart uses the injected memory ({marker})")
        recalled = recall_in_container(a_id, question, base)
        check(any(marker in m for m in recalled), f"recall still returns {marker} after restart")

        print("\n6. User B asks the same question")
        other = ask(tok_b, question)
        print(f"  memories_used={other['memories_used']} reply={other['reply'][:140]!r}")
        b_recall = recall_in_container(b_id, question, base)
        check(not any(marker in m for m in b_recall), "B's recall has no trace of A's fact")
        check(marker not in other["reply"], "B's reply does not reveal A's fact")
        # B may have memories of their own from earlier runs; A's must never appear.
        if args.auth == "fake":
            check(other["memories_used"] == 0, "B (fresh test user) gets memories_used == 0")
    finally:
        if args.auth == "fake":
            print("\nCleaning up fake verification users and restoring normal auth...")
            cleanup_in_container(["test-verify-a", "test-verify-b"], base)
            compose(["up", "-d", "api"], BASE_COMPOSE)
            wait_healthy()

    return check.result()


if __name__ == "__main__":
    sys.exit(main())
