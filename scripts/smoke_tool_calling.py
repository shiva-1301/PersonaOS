"""Tool-calling smoke test across chat models (decision D1a).

Binds two small tools and checks each model picks the right tool with sensible arguments,
does not call tools for chit-chat, and handles a two-task request. Uses the real providers
from .env (GEMINI_API_KEY, OLLAMA_BASE_URL). Fake data only.

PowerShell:
    .venv\\Scripts\\python.exe scripts\\smoke_tool_calling.py
    .venv\\Scripts\\python.exe scripts\\smoke_tool_calling.py --models ollama:qwen2.5:7b --trials 3
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.services.llm import get_chat_model, invoke_with_backoff  # noqa: E402

DEFAULT_MODELS = [
    "gemini:gemini-2.5-flash",
    "gemini:gemini-3.5-flash",
    "gemini:gemini-3.8-flash",
    "ollama:qwen2.5:7b",
    "ollama:llama3.1:8b",
]


@tool
def add_task(title: str, due_date: str | None = None) -> str:
    """Add a task to the user's to-do list. due_date is ISO format YYYY-MM-DD if known."""
    return "ok"


@tool
def list_tasks(status: str = "todo") -> str:
    """List the user's tasks filtered by status: todo, doing or done."""
    return "[]"


SYSTEM = (
    "You are a personal assistant. Today is 2026-10-01. Use tools for anything about the "
    "user's tasks. Answer general questions directly without tools."
)

CASES = [
    (
        "add_one",
        "Add a task to buy milk tomorrow.",
        lambda c: (
            len(c) == 1
            and c[0]["name"] == "add_task"
            and "milk" in c[0]["args"].get("title", "").lower()
            and c[0]["args"].get("due_date") == "2026-10-02"
        ),
    ),
    (
        "list",
        "What tasks do I still have to do?",
        lambda c: len(c) == 1 and c[0]["name"] == "list_tasks",
    ),
    ("no_tool", "What is the capital of France?", lambda c: len(c) == 0),
    (
        "add_two",
        "Add two tasks: call mom and pay rent.",
        lambda c: len(c) == 2 and all(x["name"] == "add_task" for x in c),
    ),
]


def run(spec: str, trials: int, settings: Settings) -> dict:
    provider, model = spec.split(":", 1)
    s = settings.model_copy(update={"LLM_PROVIDER": provider, "LLM_MODEL": model})
    llm = get_chat_model(s).bind_tools([add_task, list_tasks])
    passed, total, latencies, notes = 0, 0, [], []
    for name, prompt, check in CASES:
        for _ in range(trials):
            total += 1
            t0 = time.perf_counter()
            try:
                msg = invoke_with_backoff(
                    llm,
                    [SystemMessage(SYSTEM), HumanMessage(prompt)],
                    attempts=s.LLM_RATE_LIMIT_ATTEMPTS,
                )
            except Exception as exc:  # report and continue
                notes.append(f"{name}: ERROR {type(exc).__name__}: {str(exc)[:120]}")
                continue
            latencies.append(time.perf_counter() - t0)
            calls = [{"name": c["name"], "args": c["args"]} for c in msg.tool_calls]
            if check(calls):
                passed += 1
            else:
                notes.append(f"{name}: got {calls or repr(str(msg.content)[:80])}")
    lat = sorted(latencies)
    return {
        "model": spec,
        "passed": passed,
        "total": total,
        "median_s": round(lat[len(lat) // 2], 2) if lat else None,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--trials", type=int, default=2)
    args = parser.parse_args()
    settings = Settings()
    for spec in args.models:
        r = run(spec, args.trials, settings)
        print(f"{r['model']:32} {r['passed']:>2}/{r['total']:<2} median {r['median_s']}s")
        for n in r["notes"]:
            print(f"    - {n}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
