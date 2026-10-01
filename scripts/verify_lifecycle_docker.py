"""Memory lifecycle + privacy against the RUNNING Docker stack (real models).

  1. Cron endpoint: 503 if CRON_SECRET is not set; otherwise wrong secret -> 401, right -> 200.
  2. Supersession: "I study best after 6 pm" then "Actually, I now prefer studying in the
     mornings" -> the old memory is superseded and no longer recalled.
  3. No false supersession: "My ML exam is on 12 December" survives "I'm taking the
     Andrew Ng ML course".
  4. Decay: simulate 60 days for the TEST USER ONLY (in the container) -> archived and
     no longer recalled; GET /memory/health shows it.
  5. DELETE /memory/{id}: vector, lifecycle row and Mem0 SQLite history rows all gone.
  6. DELETE /me/data: every store is empty for user A (Postgres, Mem0 vectors, Mem0
     SQLite history/messages, Chroma document chunks); user B is untouched.

PowerShell:
  .venv\\Scripts\\python.exe scripts\\verify_lifecycle_docker.py
"""

import json
import os
import sys
import time

from docker_helpers import (
    BASE_COMPOSE,
    FAKE_COMPOSE,
    ROOT,
    Checks,
    cleanup_in_container,
    compose,
    container_json,
    http,
    upload,
    wait_healthy,
)

check = Checks()
A, B = "test-verify-a", "test-verify-b"


def ok(result, expected=200):
    status, body = result
    if status != expected:
        raise SystemExit(f"expected {expected}, got {status}: {body}")
    return body


def tell(token: str, text: str) -> dict:
    """Chat, then wait until this turn's memory extraction (and supersession) is done."""
    body = ok(http("POST", "/chat", token, {"message": text}))
    for _ in range(300):
        detail = ok(http("GET", f"/chat/sessions/{body['session_id']}", token))
        state = next(
            m["memory_status"] for m in detail["messages"] if m["id"] == body["message_id"]
        )
        if state != "pending":
            print(f"  > {text}  (memory {state})")
            return body
        time.sleep(1)
    raise SystemExit("memory extraction timed out")


def memories(token: str) -> list[dict]:
    return ok(http("GET", "/memory", token))


def find(items: list[dict], *words: str) -> list[dict]:
    return [m for m in items if all(w in m["text"].lower() for w in words)]


def recall(user_id: str, query: str) -> list[str]:
    return container_json(
        f"print(json.dumps([m.text for m in svc.memory.recall(db, uuid.UUID({user_id!r}), "
        f"{query!r}, k=s.MEMORY_RECALL_K)]))",
        FAKE_COMPOSE,
    )


def cron_secret() -> str | None:
    from dotenv import dotenv_values

    return {**dotenv_values(ROOT / ".env"), **os.environ}.get("CRON_SECRET") or None


def main() -> int:
    print("Starting api with fake auth (docker-compose.fake-auth.yml)...")
    compose(["up", "-d", "api"], FAKE_COMPOSE)
    wait_healthy()
    cleanup_in_container([A, B], FAKE_COMPOSE)
    try:
        a_id = ok(http("GET", "/me", A))["id"]
        b_id = ok(http("GET", "/me", B))["id"]

        print("\n1. Cron endpoint")
        path = "/internal/jobs/memory-lifecycle"
        secret = cron_secret()
        if secret is None:
            check(http("POST", path)[0] == 503, "503 while CRON_SECRET is not configured")
            print("    (add CRON_SECRET to .env and recreate the api to test the success path)")
        else:
            wrong = http("POST", path, None, None)
            check(wrong[0] == 401, "no secret -> 401")
            import urllib.request

            def post(secret_value):
                req = urllib.request.Request(
                    "http://localhost:8000" + path,
                    method="POST",
                    headers={"X-Cron-Secret": secret_value},
                )
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as exc:
                    return exc.code, None

            check(post("definitely-wrong")[0] == 401, "wrong secret -> 401")
            status, stats = post(secret)
            check(status == 200, f"right secret -> 200 {stats}")

        print("\n2. Supersession (real model)")
        tell(A, "I study best after 6 pm.")
        tell(A, "My ML exam is on 12 December.")
        tell(A, "Actually, I now prefer studying in the mornings.")
        items = memories(A)
        for m in items:
            print(f"    [{m['state']:10}] {m['text']}")
        # The old fact mentions 6 pm and not mornings (the new fact may quote the old
        # value, e.g. "...has changed from studying best after 6 pm").
        old = [m for m in find(items, "6 pm") if "morning" not in m["text"].lower()]
        new = find(items, "morning")
        check(
            len(old) == 1 and old[0]["state"] == "superseded",
            "the 'after 6 pm' memory is superseded",
        )
        check(
            bool(old) and bool(new) and old[0]["superseded_by"] == new[0]["id"],
            "superseded_by points to the new preference",
        )
        recalled = recall(a_id, "When do I prefer to study?")
        print(f"    recall: {recalled}")
        check(bool(old) and old[0]["text"] not in recalled, "superseded memory is not recalled")
        check(any("morning" in r.lower() for r in recalled), "the new preference is recalled")

        print("\n3. No false supersession")
        tell(A, "I'm taking the Andrew Ng ML course.")
        exam = find(memories(A), "12 december") or find(memories(A), "exam")
        check(
            bool(exam) and all(m["state"] == "active" for m in exam),
            "exam date memory stays active",
        )

        print("\n4. Decay: 60 days later (test user only)")
        stats = container_json(
            "from datetime import UTC, datetime, timedelta\n"
            "from app.jobs.memory_lifecycle import run_memory_lifecycle\n"
            f"print(json.dumps(run_memory_lifecycle(db, svc.memory, now=datetime.now(UTC) + "
            f"timedelta(days=60), only_user=uuid.UUID({a_id!r}))))",
            FAKE_COMPOSE,
        )
        print(f"    job: {stats}")
        live = [m for m in memories(A) if m["state"] != "superseded"]
        check(
            bool(live) and all(m["state"] == "archived" for m in live), "all live memories archived"
        )
        check(recall(a_id, "When is my ML exam?") == [], "archived memories are not recalled")
        health = ok(http("GET", "/memory/health", A))
        check(health["by_state"]["archived"] == len(live), f"health by_state {health['by_state']}")

        print("\n5. Delete one memory")
        target = exam[0]["id"] if exam else memories(A)[0]["id"]
        check(http("DELETE", f"/memory/{target}", B)[0] == 404, "B cannot delete A's memory")
        check(http("DELETE", f"/memory/{target}", A)[0] == 204, "A deletes it")
        gone = container_json(
            f"h, _ = svc.memory.history_counts(uuid.UUID({a_id!r}), [{target!r}])\n"
            f"v = svc.memory.vector_store.get(vector_id={target!r}) is not None\n"
            "print(json.dumps({'vector': v, 'history': h}))",
            FAKE_COMPOSE,
        )
        check(gone == {"vector": False, "history": 0}, f"vector and SQLite history gone {gone}")

        print("\n6. Delete all my data")
        _, doc = upload(ROOT / "test_files" / "ML_Notes_1_Linear_Regression.txt", A)
        goal = ok(http("POST", "/goals", A, {"title": "Finish ML course"}), 201)
        ok(http("POST", "/tasks", A, {"title": "Revise", "goal_id": goal["id"]}), 201)
        tell(B, "I study best after 6 pm.")
        upload(ROOT / "test_files" / "ML_Notes_2_Decision_Trees.docx", B)
        time.sleep(3)  # let both uploads finish ingesting
        ids = [m["id"] for m in memories(A)] + [target]
        b_ids = [m["id"] for m in memories(B)]
        snapshot = (
            "from app.services.privacy_service import remaining_data\n"
            "print(json.dumps(remaining_data(db, svc, uuid.UUID({uid!r}), {ids!r})))"
        )
        before = container_json(snapshot.format(uid=a_id, ids=ids), FAKE_COMPOSE)
        b_before = container_json(snapshot.format(uid=b_id, ids=b_ids), FAKE_COMPOSE)
        print(f"    A before: {before}")
        check(
            http("DELETE", "/me/data", A, {"confirm": "yes please"})[0] == 422,
            "wrong confirmation -> 422",
        )
        result = ok(http("DELETE", "/me/data", A, {"confirm": "DELETE MY DATA"}))
        print(f"    deleted: {result['deleted']}")
        after = container_json(snapshot.format(uid=a_id, ids=ids), FAKE_COMPOSE)
        print(f"    A after:  {after}")
        check(all(v == 0 for v in after.values()), "A: zero rows/vectors/history in every store")
        check(
            after.get("chroma.document_chunks") == 0
            and after.get("mem0.sqlite_history") == 0
            and after.get("mem0.sqlite_messages") == 0,
            "includes Chroma chunks and Mem0 SQLite",
        )
        b_after = container_json(snapshot.format(uid=b_id, ids=b_ids), FAKE_COMPOSE)
        check(b_after == b_before, "user B untouched")
        check(
            any("6 pm" in r for r in recall(b_id, "When do I study best?")),
            "B still recalls their memory",
        )
    finally:
        print("\nCleaning up fake verification users and restoring normal auth...")
        cleanup_in_container([A, B], FAKE_COMPOSE)
        compose(["up", "-d", "api"], BASE_COMPOSE)
        wait_healthy()
    return check.result()


if __name__ == "__main__":
    sys.exit(main())
