"""End-to-end goals/tasks/planner check against the RUNNING Docker stack (real models).

  1. User A (timezone Asia/Kolkata) tells the assistant "I study best after 6 pm";
     wait for memory extraction.
  2. A creates a goal ending in 4 weeks and asks for a plan at 6 hours/week.
     Checks: tasks linked to the goal, all inside [now, target date], weekly minutes <= 360,
     the remembered preference was used; reports how many sessions start at/after 18:00.
  3. Progress and completed_at: mark 2 tasks done -> progress 2/N; back to todo clears it.
  4. Filter: ?due=this_week only returns tasks due in the current Mon-Sun week (Kolkata).
  5. User B: 404 on A's goal, tasks and plan endpoint; B's lists are empty.
  6. `docker compose restart api`: goal, tasks and progress are unchanged.

PowerShell:
  .venv\\Scripts\\python.exe scripts\\verify_planner_docker.py
"""

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from docker_helpers import (
    BASE_COMPOSE,
    FAKE_COMPOSE,
    Checks,
    cleanup_in_container,
    compose,
    http,
    wait_healthy,
)

check = Checks()
TZ = ZoneInfo("Asia/Kolkata")


def ok(status_and_body, expected=200):
    status, body = status_and_body
    if status != expected:
        raise SystemExit(f"expected {expected}, got {status}: {body}")
    return body


def wait_for_memory(token, session_id, message_id, timeout=300) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        detail = ok(http("GET", f"/chat/sessions/{session_id}", token))
        state = next(m["memory_status"] for m in detail["messages"] if m["id"] == message_id)
        if state == "done":
            return time.monotonic() - start
        if state == "failed":
            raise SystemExit("memory extraction failed")
        time.sleep(1)
    raise SystemExit("memory extraction timed out")


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    base = FAKE_COMPOSE
    tok_a, tok_b = "test-verify-a", "test-verify-b"
    print("Starting api with fake auth (docker-compose.fake-auth.yml)...")
    compose(["up", "-d", "api"], base)
    wait_healthy()
    cleanup_in_container([tok_a, tok_b], base)

    try:
        print("\n1. Preference -> memory")
        ok(http("PATCH", "/me", tok_a, {"timezone": "Asia/Kolkata"}))
        ok(http("GET", "/me", tok_b))
        chat = ok(
            http(
                "POST", "/chat", tok_a, {"message": "I'm bad at mornings. I study best after 6 pm."}
            )
        )
        secs = wait_for_memory(tok_a, chat["session_id"], chat["message_id"])
        print(f"  memory extracted in {secs:.1f}s")

        print("\n2. Goal + study plan (real LLM)")
        target = (datetime.now(TZ) + timedelta(days=27)).date()
        goal = ok(
            http(
                "POST",
                "/goals",
                tok_a,
                {"title": "Finish the Machine Learning course", "target_date": target.isoformat()},
            ),
            201,
        )
        t0 = time.monotonic()
        status, plan = http("POST", f"/goals/{goal['id']}/plan", tok_a, {"hours_per_week": 6})
        secs = time.monotonic() - t0
        check(status == 201, f"plan created ({status}) in {secs:.1f}s")
        if status != 201:
            print("   ", plan)
            return check.result()
        tasks = plan["tasks"]
        latest = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=TZ)
        now = datetime.now(UTC) - timedelta(minutes=5)
        weeks: dict = {}
        evening = 0
        for t in tasks:
            due = datetime.fromisoformat(t["due_at"])
            local = due.astimezone(TZ)
            weeks[local.date() - timedelta(days=local.weekday())] = (
                weeks.get(local.date() - timedelta(days=local.weekday()), 0) + t["est_minutes"]
            )
            evening += local.hour >= 18
            print(f"    {local:%a %d %b %H:%M}  {t['est_minutes']:>3} min  {t['title']}")
        print(f"  preferences used: {plan['used_preferences']}")
        print(f"  adjustments: {plan['adjustments'] or 'none'}")
        check(len(tasks) >= 3, f"{len(tasks)} sessions")
        check(all(t["goal_id"] == goal["id"] for t in tasks), "all tasks linked to the goal")
        check(
            all(now <= datetime.fromisoformat(t["due_at"]) < latest for t in tasks),
            "all sessions between now and the target date",
        )
        check(max(weeks.values()) <= 360, f"weekly minutes <= 360 (max {max(weeks.values())})")
        check(
            any("6 pm" in p for p in plan["used_preferences"]),
            "remembered 'after 6 pm' preference passed to the planner",
        )
        print(f"  {evening}/{len(tasks)} sessions start at or after 18:00 Kolkata time")

        print("\n3. Progress and completed_at")
        for t in tasks[:2]:
            done = ok(http("PATCH", f"/tasks/{t['id']}", tok_a, {"status": "done"}))
            check(done["completed_at"] is not None, "done sets completed_at")
        progress = ok(http("GET", f"/goals/{goal['id']}", tok_a))["progress"]
        check(progress["done"] == 2 and progress["total"] == len(tasks), f"progress {progress}")
        back = ok(http("PATCH", f"/tasks/{tasks[1]['id']}", tok_a, {"status": "todo"}))
        check(back["completed_at"] is None, "leaving done clears completed_at")

        print("\n4. This week's tasks (Kolkata calendar week)")
        today = datetime.now(TZ).date()
        monday = today - timedelta(days=today.weekday())
        this_week = ok(http("GET", "/tasks?due=this_week", tok_a))
        in_week = [
            t
            for t in tasks
            if monday
            <= datetime.fromisoformat(t["due_at"]).astimezone(TZ).date()
            < monday + timedelta(days=7)
        ]
        check(
            {t["id"] for t in this_week} == {t["id"] for t in in_week},
            f"?due=this_week returns exactly the {len(in_week)} session(s) in this week",
        )
        check(ok(http("GET", "/tasks?due=overdue", tok_a)) == [], "nothing overdue")

        print("\n5. User B isolation")
        for method, path, body in [
            ("GET", f"/goals/{goal['id']}", None),
            ("PATCH", f"/goals/{goal['id']}", {"title": "hijack"}),
            ("POST", f"/goals/{goal['id']}/plan", {"hours_per_week": 2}),
            ("GET", f"/tasks/{tasks[0]['id']}", None),
            ("PATCH", f"/tasks/{tasks[0]['id']}", {"status": "done"}),
            ("DELETE", f"/tasks/{tasks[0]['id']}", None),
        ]:
            check(
                http(method, path, tok_b, body)[0] == 404, f"B {method} {path.split('/')[1]} -> 404"
            )
        check(
            ok(http("GET", "/goals", tok_b)) == [] and ok(http("GET", "/tasks", tok_b)) == [],
            "B's goal and task lists are empty",
        )

        print("\n6. docker compose restart api")
        compose(["restart", "api"], base)
        print(f"  healthy again after {wait_healthy():.1f}s")
        after = ok(http("GET", f"/goals/{goal['id']}", tok_a))
        check(
            after["progress"] == {**progress, "done": 1, "ratio": round(1 / len(tasks), 4)},
            f"progress after restart {after['progress']}",
        )
        check(
            len(ok(http("GET", f"/tasks?goal_id={goal['id']}", tok_a))) == len(tasks),
            "all tasks still present",
        )
    finally:
        print("\nCleaning up fake verification users and restoring normal auth...")
        cleanup_in_container([tok_a, tok_b], base)
        compose(["up", "-d", "api"], BASE_COMPOSE)
        wait_healthy()

    return check.result()


if __name__ == "__main__":
    sys.exit(main())
