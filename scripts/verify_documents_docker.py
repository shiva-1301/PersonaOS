"""End-to-end document/RAG check against the RUNNING Docker stack (real models).

For every PDF/DOCX/TXT in --files-dir (default: test_files/):
  1. User A uploads it (202 "processing"); poll until ready/failed; report ingest time.
     Files with "notext"/"scanned" in the name must FAIL with a clear reason.
  2. Retrieval: a real sentence taken from the stored chunks (read inside the container)
     is searched; the top hit must be that document.
  3. Chat: "What do my notes say about <topic from filename>?" must cite that file.
  4. User B: same search -> nothing; same chat -> no sources; GET A's doc -> 404.
  5. `docker compose restart api`; A's search still finds the document (persistence).
  6. Summarize one document with the real LLM.
  7. Delete one document: 204, its Chroma chunk count becomes 0, other documents intact.

Auth modes as in verify_memory_docker.py (default --auth fake, cleaned up afterwards).

PowerShell:
  .venv\\Scripts\\python.exe scripts\\verify_documents_docker.py
"""

import argparse
import getpass
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

from docker_helpers import (
    BASE_COMPOSE,
    FAKE_COMPOSE,
    ROOT,
    Checks,
    cleanup_in_container,
    compose,
    container_json,
    firebase_token,
    http,
    upload,
    wait_healthy,
)

check = Checks()
ALLOWED = {".pdf", ".docx", ".txt"}


def expect_failure(path: Path) -> bool:
    name = path.name.lower()
    return "notext" in name or "scanned" in name


def topic_of(path: Path) -> str:
    """ML_Notes_2_Decision_Trees.docx -> 'Decision Trees'."""
    words = path.stem.replace("-", "_").split("_")
    for i, w in enumerate(words):
        if w.isdigit():
            words = words[i + 1 :] or words
            break
    return " ".join(words)


def wait_for_document(token: str, doc_id: str, timeout: float) -> tuple[dict, float]:
    start = time.monotonic()
    while True:
        _, doc = http("GET", f"/documents/{doc_id}", token)
        if doc["status"] != "processing":
            return doc, time.monotonic() - start
        if time.monotonic() - start > timeout:
            raise SystemExit(f"document still processing after {timeout}s")
        time.sleep(0.5)


def search(token: str, query: str, k: int = 3) -> list[dict]:
    q = urllib.parse.urlencode({"q": query[:1000], "k": k})
    status, hits = http("GET", f"/documents/search?{q}", token)
    if status != 200:
        raise SystemExit(f"search returned {status}: {hits}")
    return hits


def probe_sentence(user_id: str, doc_id: str, base: list[str]) -> str:
    """A real sentence from the stored chunks of this document (read in the container)."""
    chunks = container_json(
        f"print(json.dumps(svc.rag.document_chunks(uuid.UUID({user_id!r}), "
        f"uuid.UUID({doc_id!r}))))",
        base,
    )
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(chunks)) if len(s.split()) >= 8
    ]
    return max(sentences, key=len) if sentences else " ".join(chunks)[:300]


def chunk_count(user_id: str, doc_id: str | None, base: list[str]) -> int:
    doc_arg = f"uuid.UUID({doc_id!r})" if doc_id else "None"
    return container_json(
        f"print(json.dumps(svc.rag.count(uuid.UUID({user_id!r}), {doc_arg})))", base
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify document RAG in Docker")
    parser.add_argument("--auth", choices=["fake", "firebase"], default="fake")
    parser.add_argument("--email-a")
    parser.add_argument("--email-b")
    parser.add_argument("--files-dir", type=Path, default=ROOT / "test_files")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    files = sorted(p for p in args.files_dir.glob("*") if p.suffix.lower() in ALLOWED)
    if not files:
        raise SystemExit(f"No PDF/DOCX/TXT files in {args.files_dir}")

    base = FAKE_COMPOSE if args.auth == "fake" else BASE_COMPOSE
    if args.auth == "fake":
        print("Starting api with fake auth (docker-compose.fake-auth.yml)...")
        compose(["up", "-d", "api"], base)
        tok_a, tok_b = "test-verify-a", "test-verify-b"
        wait_healthy()
        cleanup_in_container([tok_a, tok_b], base)
    else:
        if not (args.email_a and args.email_b):
            raise SystemExit("--auth firebase needs --email-a and --email-b")
        pw_a = os.environ.get("PERSONAOS_PASSWORD_A") or getpass.getpass("Password A: ")
        pw_b = os.environ.get("PERSONAOS_PASSWORD_B") or getpass.getpass("Password B: ")
        tok_a, tok_b = firebase_token(args.email_a, pw_a), firebase_token(args.email_b, pw_b)
    wait_healthy()
    a_id = http("GET", "/me", tok_a)[1]["id"]
    http("GET", "/me", tok_b)

    ready: dict[str, dict] = {}  # doc_id -> {"path", "probe"}
    try:
        print("\n1. Upload and ingest")
        for path in files:
            status, doc = upload(path, tok_a)
            if status != 202:
                check(False, f"{path.name}: upload returned {status} {doc}")
                continue
            doc, secs = wait_for_document(tok_a, doc["id"], args.timeout)
            print(
                f"  {path.name}: {doc['status']} in {secs:.1f}s, chunks={doc['chunk_count']}"
                + (f", error={doc['error']!r}" if doc["error"] else "")
            )
            if expect_failure(path):
                check(
                    doc["status"] == "failed" and "No extractable text" in (doc["error"] or ""),
                    f"{path.name} fails with a clear 'no extractable text' reason",
                )
            else:
                check(doc["status"] == "ready" and doc["chunk_count"] >= 1, f"{path.name} ready")
                if doc["status"] == "ready":
                    ready[doc["id"]] = {"path": path}
        if not ready:
            raise SystemExit("no document became ready")

        print("\n2-3. Retrieval and chat citations (user A)")
        for doc_id, info in ready.items():
            info["probe"] = probe_sentence(a_id, doc_id, base)
            hits = search(tok_a, info["probe"])
            check(
                bool(hits) and hits[0]["document_id"] == doc_id,
                f"search ranks {info['path'].name} first "
                f"(relevance {hits[0]['relevance'] if hits else 0:.3f})",
            )
            question = f"What do my notes say about {topic_of(info['path'])}?"
            t0 = time.monotonic()
            _, reply = http("POST", "/chat", tok_a, {"message": question})
            cited = [s["filename"] for s in reply["sources"]]
            print(f"  Q: {question}  ({time.monotonic() - t0:.1f}s)  cited={cited}")
            print(f"     A: {reply['reply'][:160]!r}")
            check(info["path"].name in cited, f"chat cites {info['path'].name}")

        print("\n4. User B isolation")
        for doc_id, info in ready.items():
            check(
                search(tok_b, info["probe"]) == [],
                f"B's search finds nothing of {info['path'].name}",
            )
            check(http("GET", f"/documents/{doc_id}", tok_b)[0] == 404, "B gets 404 for A's doc")
        _, reply_b = http(
            "POST", "/chat", tok_b, {"message": f"What do my notes say about {topic_of(files[0])}?"}
        )
        check(reply_b["sources"] == [], "B's chat has no document sources")

        print("\n5. docker compose restart api")
        compose(["restart", "api"], base)
        print(f"  healthy again after {wait_healthy():.1f}s")
        for doc_id, info in ready.items():
            hits = search(tok_a, info["probe"])
            check(
                bool(hits) and hits[0]["document_id"] == doc_id,
                f"after restart, search still finds {info['path'].name}",
            )

        print("\n6. Summary (real LLM)")
        doc_id, info = next(iter(ready.items()))
        t0 = time.monotonic()
        status, summary = http("POST", f"/documents/{doc_id}/summarize", tok_a)
        print(f"  {info['path'].name} ({time.monotonic() - t0:.1f}s):")
        print("   ", (summary or {}).get("summary", summary)[:400].replace("\n", "\n    "))
        check(status == 200 and len(summary["summary"]) > 40, "summary generated")
        stored = http("GET", f"/documents/{doc_id}", tok_a)[1]["summary"]
        check(stored == summary["summary"], "summary stored on the document")

        print("\n7. Delete")
        total_before = chunk_count(a_id, None, base)
        victim = chunk_count(a_id, doc_id, base)
        status, _ = http("DELETE", f"/documents/{doc_id}", tok_a)
        check(status == 204, "delete returns 204")
        check(chunk_count(a_id, doc_id, base) == 0, f"Chroma chunks for the doc: {victim} -> 0")
        check(chunk_count(a_id, None, base) == total_before - victim, "other documents intact")
        check(http("GET", f"/documents/{doc_id}", tok_a)[0] == 404, "deleted doc returns 404")
    finally:
        if args.auth == "fake":
            print("\nCleaning up fake verification users and restoring normal auth...")
            cleanup_in_container(["test-verify-a", "test-verify-b"], base)
            compose(["up", "-d", "api"], BASE_COMPOSE)
            wait_healthy()

    return check.result()


if __name__ == "__main__":
    sys.exit(main())
