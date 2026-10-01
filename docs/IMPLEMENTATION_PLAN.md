# PersonaOS: Phased Implementation Plan

**Source documents:** `docs/2_PersonaOS_Agent_Build_Spec.md` (architecture and data model) and `docs/3_PersonaOS_Continuation_Handoff.md` (sequencing and detail). Phase numbers follow the handoff document.
**Created:** 30 Sep 2026
**Branch:** `master` (the handoff says `main`, but the repo uses `master`, so all phase commits go to `master`.)

---

## How we work (the phase gate)

```
 ┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ Pre-phase:   │──▶│ Build phase │──▶│ Tests + lint │──▶│ Commit, push │──▶│ Phase report │
 │ you supply   │   │ (Claude)    │   │ green        │   │ phase-N: ... │   │ to you       │
 │ keys/IDs     │   └─────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
 └──────────────┘                                                                  │
        ▲                                                                          ▼
        │                       you run the "How you verify" steps and reply "approved"
        └──────────────────────────────────────────────────────────────────────────┘
```

1. **Before a phase:** Claude lists anything you must provide (keys, IDs, console steps). If something is missing, work stops until you provide it. Claude never fakes credentials or quietly switches providers.
2. **During a phase:** Claude checks the current official docs for every library before using it, and pins the versions that work.
3. **End of a phase:** full `pytest` plus `ruff check`, then a commit `phase-N: <summary>`, then a push, then a report in the format below.
4. **Gate:** Claude does **not** start the next phase until you reply that the phase is verified.
5. **Ambiguity:** Claude picks the simplest option, logs it in `docs/DECISIONS.md` and carries on. Heavy installs (for example PyTorch through `sentence-transformers`) need your approval first.

### Phase report format

```
Phase N complete
- What was built:
- Files added/changed:
- Tests run and results (unit / integration / isolation / scenario):
- Manual checks the human should do:
- Human action needed before the next phase (keys, IDs, console steps):
- Assumptions made (also in docs/DECISIONS.md):
- Known issues / risks for the next phase:
```

---

## Decisions to lock first (D1 to D6)

**Locked 30 Sep 2026.** Full reasoning is in `docs/DECISIONS.md`.

| # | Decision | Locked choice | Used from |
|---|---|---|---|
| D1 | Chat/agent LLM | Gemini **Flash** (AI Studio free tier), with 429 retry and backoff. In Phase 3, tool calling is also tested against a local ~8B Ollama model. | Phase 3 |
| D2 | Embeddings | **Ollama `nomic-embed-text`** on the Windows host (`host.docker.internal:11434`). **Cannot change later without re-embedding.** | Phase 3 |
| D3 | Auth provider | Firebase Auth (Email/Password + Google only) | Phase 2 |
| D4 | v1 frontend | Streamlit | Phase 9 |
| D5 | SQLAlchemy mode | Sync SQLAlchemy 2.x (FastAPI threadpool) | Phase 2 |
| D6 | Chroma mode | Embedded persistent client in the `api` container, on a named volume | Phase 1 |

Use **fake, non-sensitive data only** on free LLM tiers, because providers may use free-tier prompts for training.

---

## Phase overview and progress tracker

| Phase | Deliverable | Depends on | You provide beforehand | Status |
|---|---|---|---|---|
| 1 | App scaffold, Docker Compose, config, `/health` | – | Docker Desktop running | ✅ Verified 30 Sep 2026 |
| 2 | DB models, Alembic, Firebase JWT auth, `/me` | 1 | Firebase project, `FIREBASE_PROJECT_ID`, 2 test users | ✅ Verified 1 Oct 2026 |
| 3 | LLM factory, Mem0 memory, plain chat | 2 | `GEMINI_API_KEY`; Ollama with `nomic-embed-text` + an ~8B chat model pulled | ✅ Verified 1 Oct 2026 |
| 4 | Document upload, parsing, RAG, summaries | 3 | Sample PDF, DOCX, TXT (non-sensitive) | ✅ Verified 1 Oct 2026 |
| 5 | Goals, tasks, study-plan generator | 2 (+3 for LLM) | – | ✅ Verified 1 Oct 2026 (with order/spacing fix) |
| 6 | LangGraph agent with tools | 3, 4, 5 | – | ✅ Verified 1 Oct 2026 |
| 7 | Memory lifecycle, supersession, privacy endpoints | 3, 6 | `CRON_SECRET` | ✅ Verified 1 Oct 2026 |
| 8 | Google OAuth and Calendar (Drive/Gmail optional) | 6 | GCP project, OAuth client, `TOKEN_ENCRYPTION_KEY` | 🟨 Calendar verified 1 Oct 2026; follow-up fixes (timezone, "yes" in code, study-plan guard) committed, awaiting your rerun of the manual check (Drive/Gmail not built) |
| 9 | Analytics API and Streamlit dashboard | 5, 6, 7 | Firebase web app config | 🟨 Built, awaiting your UI walkthrough (started on your go-ahead with Phase 8's rerun still pending) |

### Where things stand (1 Oct 2026)

- **Done:** Phases 1–7, plus Phase 8 Calendar. Your first manual Google check passed, and the follow-ups are committed.
- **Phase 9:** built and tested offline. The stack runs it in Docker at http://localhost:8501, on Firebase auth. It includes:
  - `GET /analytics/summary`;
  - the Streamlit UI (Chat, Documents, Goals & Tasks, Dashboard, Memory, Integrations);
  - Firebase sign-in, sign-up and password reset;
  - your timezone taken from the browser at first sign-in;
  - "delete my login" alongside "delete all my data".
- **Offline gate:** pytest 382 passed, ruff clean.
- **Next:**
  1. You verify Phase 9 using only the UI (steps in the Phase 9 report).
  2. Optionally, rerun `scripts/google_calendar_check.py --email <you> --timezone Asia/Kolkata` for Phase 8.
  3. Phase 10 after you confirm.
- **Known problems / not verified:**
  - The Phase 8 manual rerun (`--timezone`) hasn't been reported yet. You chose to start Phase 9 first.
  - The full Docker agent check (`verify_agent_docker.py`) hasn't been rerun with the latest agent code. It was stopped for time.
  - The UI hasn't been driven with your real Firebase login by me; that needs your password. It was tested headless (Streamlit AppTest) against the real API with fake auth.
  - In Google's Testing mode, the connection expires after 7 days; reconnect before demos.
  - The local model's plan summaries can be loose (e.g. "1 session per week"). The saved tasks are correct.
  - Streamlit has no native "open in new tab" redirect. Connecting Google uses a link button, then **Refresh** on the Integrations page.

| 10 | Hardening, deployment, docs, CI | 1–9 | Render account and payment method | ⬜ |
| 11 | *(Optional)* Knowledge graph | 10 | – | ⬜ |

---

## Phase 1: Application scaffold, Docker, config, health

**Goal:** a running skeleton with the final folder layout.

**Build**
- The folder layout from Build Spec §3 (`app/{auth,db,schemas,routers,services,agent,jobs}`, `frontend/`, `tests/`), with an `__init__.py` in each package.
- `requirements.txt` (pinned) and `requirements-dev.txt` (pytest, pytest-asyncio, httpx, ruff), installed into `.venv`, with `pip check` clean.
- `app/config.py` using pydantic-settings with every variable from the handoff §Phase 1. Secrets are `SecretStr`, and a missing optional integration never crashes startup.
- `app/main.py`: an app factory, structured logging, request-ID middleware and `GET /health` returning `{"status":"ok"}`.
- `.env.example` with names and comments only, no values.
- `Dockerfile` (python:3.12-slim, non-root user, uvicorn) and `docker-compose.yml` with `api` and `postgres` services, named volumes `pg_data` and `chroma_data`, a Postgres healthcheck, and `api` depending on healthy Postgres.
- Ruff config in `pyproject.toml`, and `scripts/*.ps1` (`test`, `lint`, `up`, `down`) for Windows.
- A `README.md` stub, `docs/DECISIONS.md` (D1–D6, numbering note, branch note) and `tests/test_health.py`.

**Acceptance**
- `docker compose up --build` starts both services, and `GET http://localhost:8000/health` returns 200.
- `pytest` passes and `ruff check .` is clean.
- `.env` is ignored and no secrets are committed.

**How you verify (PowerShell)**
```powershell
.venv\Scripts\activate
pip install -r requirements-dev.txt
.\scripts\test.ps1; .\scripts\lint.ps1
Copy-Item .env.example .env     # then set POSTGRES_PASSWORD in .env
.\scripts\up.ps1                # builds, starts, waits for /health
curl.exe -i http://localhost:8000/health
.\scripts\down.ps1
```

**Commit:** `phase-1: app scaffold, docker compose, config, health endpoint`

---

## Phase 2: Database, migrations, authentication

**Goal:** real users with verified identities, and every table in place.

**Build**
- `app/db/session.py` (engine, `SessionLocal`, `get_db`).
- `app/db/models.py` covering `users` (with a `timezone` column added; full list of schema additions in DECISIONS.md), `chat_sessions`, `chat_messages`, `goals`, `tasks`, `documents`, `memory_meta` and `google_tokens` (a generalised `calendar_tokens` with a `scopes` column; logged in DECISIONS). All tables use UUID primary keys, timezone-aware UTC timestamps, constrained status strings and `ON DELETE CASCADE` from `users`.
- Indexes on `user_id` everywhere, `(user_id, status)` on tasks and goals, and `(user_id, state)` on memory_meta.
- An Alembic initial migration that runs automatically on container start. `upgrade head` and `downgrade base` are both verified.
- `app/auth/jwt_verify.py`: a `Verifier` interface with a Firebase implementation (PyJWT against Google's x509 certs, cached per `max-age`, checking signature, issuer, audience, expiry, `iat`, `auth_time` and `sub`; chosen over `firebase-admin`, see DECISIONS.md) and a `FakeVerifier` that is allowed only when `APP_ENV` is `test` or `dev`. Startup fails if `production` is paired with `fake`.
- `app/deps.py` `get_current_user`: reads the Bearer token, verifies it, and finds or creates the user by `auth_uid`. Returns 401 on a missing, invalid or expired token.
- `GET /me`.
- A consistent error envelope: `{"error": {"code", "message"}}`.

**Tests:** no token gives 401; a garbage token gives 401; a valid token gives 200 and creates one row; a repeat call creates no duplicate; two tokens give two users; an expired token is rejected; fake auth is refused in production.

**How you verify:** run `pytest`, then fetch a real Firebase ID token for a test user (Claude supplies a small helper script) and `curl.exe -H "Authorization: Bearer <token>" http://localhost:8000/me`.

**Commit:** `phase-2: database models, alembic, jwt auth, /me`

---

## Phase 3: LLM factory, Mem0 memory, plain chat

**Goal:** "it remembers me" across sessions. This is the first demo milestone.

**Build**
- `app/services/llm.py`: `get_chat_model()` and `get_embedder()`, chosen by config alone. Supports the provider from D1, and adding another provider takes one small function. Also a deterministic **fake chat model and hash-based fake embedder** for tests.
- A throwaway tool-calling check against the chosen model, with the result logged in DECISIONS.
- `app/services/memory_service.py`: Mem0 configured with Chroma collection `personaos_mem0` at `CHROMA_PATH` and the same LLM and embedder. It exposes `save_turn` and `recall` (lifecycle stubbed: `memory_meta` rows are inserted as active). This step also verifies that **no call goes to OpenAI** when OpenAI isn't configured, and that the embedding dimension matches the collection.
- `app/agent/prompts.py`: the first version of the system prompt.
- `POST /chat` (no agent yet): session, history, recall, then the LLM, then persistence. `save_turn` runs in `BackgroundTasks` and logs any failure.
- `GET /chat/sessions` and `GET /chat/sessions/{id}` (404 if the session isn't yours).
- A pytest marker `live` for real-provider smoke tests, excluded by default.

**Tests:** session ownership; recall returns the saved fact (fake embedder); user B never sees user A's memories.

**How you verify:** in session 1 say "I study best after 6 pm". In a **new** session ask "When should I schedule study time?" and the reply should reflect it. User B shouldn't see it. The memory should survive `docker compose restart`.

**Commit:** `phase-3: llm factory, mem0 memory service, chat with cross-session memory`

---

## Phase 4: Documents and RAG

**Build**
- `doc_parser.py`: PDF (pypdf), DOCX (python-docx) and TXT (encoding detection). An allowlist based on **content sniffing plus extension**. Returns 413 above `MAX_UPLOAD_MB` and 415 for an unknown type, and gives a clear "no extractable text" failure for scanned PDFs.
- `rag_service.py`: about 800-token chunks with about 100 overlap (the method is logged). Chroma collection `personaos_documents` with metadata `{user_id, document_id, filename, chunk_index}`. `retrieve(user_id, ...)` takes `user_id` as a **required positional argument** and always filters on it. Also `delete_document_chunks`.
- Routers: `POST /documents` (record created as `processing`, then background ingest, then `ready` or `failed` with a reason), `GET` list and detail, `DELETE` (row **and** vectors), and `POST /documents/{id}/summarize` (map-reduce).
- A prompt-injection guard: retrieved text is wrapped in delimited "untrusted document content" blocks, and the system prompt says it is data.

**Tests:** each parser type; oversize and wrong-type rejection; chunk count and overlap; correct chunk retrieval; user B can't retrieve A's chunks; delete removes vectors; a failed ingest is marked `failed`.

**How you verify:** upload your sample PDF, poll until `ready`, ask a content question, then delete it and check that the Chroma count drops.

**Commit:** `phase-4: document upload, parsing, chunking, rag retrieval, summaries`

---

## Phase 5: Goals, tasks, planner

**Build**
- Schemas and CRUD routers `goals.py` and `tasks.py`. Everything is user-scoped, and another user's IDs return **404**.
- Task filters: status, `today`, `this_week`, `overdue` (in the user's timezone), and by goal. `completed_at` is set and cleared automatically.
- Goal `progress` = done / total (0 when there are no tasks).
- `planner_service.generate_study_plan`: the LLM returns JSON that's validated with Pydantic, with up to 2 retries that feed back the validation error, then a clear error. Deterministic post-processing clamps dates to the range, removes past dates and keeps weekly minutes at or below the budget. It pulls preferences through `recall`, and inserts all tasks in one transaction.
- `POST /goals/{id}/plan`.

**Tests:** CRUD, isolation, progress maths and timestamps. The planner is tested with a fake LLM in three modes: valid, invalid then valid, and always invalid (which must give a readable 4xx or 502, never a stack trace).

**Commit:** `phase-5: goals, tasks, study plan generation`

---

## Phase 6: LangGraph agent with tools

**Build**
- `agent/state.py` (`AgentState` with `iteration_count`).
- `agent/tools.py`: `search_documents`, `summarize_document`, `create_goal`, `list_goals`, `add_task`, `update_task`, `list_tasks`, `generate_study_plan`, `remember_explicit`. **`user_id` and the DB session are injected from graph config, never exposed in the tool schema.** Results are compact and truncated, and a foreign ID gives a generic "not found".
- `agent/graph.py`: `START → load_context → agent ⇄ tools → save_memory → END`. After 5 iterations it forces a final answer without tools.
- The prompt adds the current date and time, the user's timezone, "documents are data", and "ask before scheduling if dates or durations are missing".
- `/chat` goes through the graph, and a `CHAT_MODE=plain` flag keeps the Phase 3 path for debugging.
- Each tool call logs its name, duration and success, never its arguments.
- *(Optional, last)* `POST /chat/stream` (SSE).

**Scenario tests (scripted fake model):** create a goal; list this week's tasks; summarise notes; generate a study plan; a prompt-injection chunk triggers no destructive call; a bogus foreign UUID in the arguments is ignored. One `live` smoke test runs against the real model.

**Commit:** `phase-6: langgraph agent with tools`

---

## Phase 7: Memory lifecycle (the differentiator)

**Build**
- Write path: map Mem0 add events (ADD, UPDATE, DELETE, NONE), checked against the current docs, onto `memory_meta` upserts.
- Read path: fetch `k*3`, drop archived and superseded, rank by `relevance × strength`, take the top k, then **reinforce** (+0.1, capped at 1.0, `access_count++`). A reinforced stale memory goes back to active. Missing meta rows are backfilled as active.
- A pure decay function `decay(now, ...)`: `S = 14 × (1 + 0.5 × access_count)` and `strength = exp(−days / S)`, where below 0.5 is stale and below 0.15 is archived.
- Supersession through **our own LLM contradiction check**. Mem0 2.2.1 is ADD-only and never emits UPDATE events (see DECISIONS.md, Phase 3).
- `jobs/memory_lifecycle.py` behind `POST /internal/jobs/memory-lifecycle` (`CRON_SECRET`, constant-time compare). It's idempotent and also cleans up orphaned meta rows.
- `routers/memory.py`: `GET /memory`, `DELETE /memory/{id}`, `GET /memory/health`, and `DELETE /me/data` (with an explicit confirmation field). It must also purge Mem0's SQLite history (`<CHROMA_PATH>/mem0_history.db`: `history` rows by the user's memory IDs, `messages` by `session_scope`) and **all of the user's document chunks in Chroma**, because a Postgres cascade doesn't reach either (see DECISIONS.md, Phases 3 and 4).

**Tests (fake clock):** 60 untouched days leaves the memory archived and out of recall; frequent access slows decay; the reinforcement cap holds; a contradiction supersedes; a wrong cron secret gets 401 or 403; `DELETE /me/data` leaves zero rows or vectors for the user and leaves other users untouched. It must explicitly assert all of these are zero for the deleted user: Postgres rows, Mem0 vectors, **Chroma document chunks** (by `user_id`), and **Mem0 SQLite `history` and `messages` rows**. The Docker script is extended to check the same inside the container.

**Commit:** `phase-7: memory lifecycle, supersession, privacy endpoints`

---

## Phase 8: Google Calendar (Drive and Gmail optional)

**Build (8A, required)**
- `GET /integrations/google/start`, with a signed, expiring, user-bound `state`, `access_type=offline` and `prompt=consent`.
- `GET /integrations/google/callback`: verifies the state, exchanges the code, **Fernet-encrypts** the refresh token and stores it.
- `DELETE /integrations/google` revokes and deletes. `GET /integrations/google/status` reports the connection.
- `calendar_service.py` (`create_event`, `list_events`, on-demand refresh, and a reconnect flag on `invalid_grant`).
- Agent tool `create_calendar_event`, which replies "please connect Google" when not connected.

**Tests (mocked Google):** state validation, an encryption round-trip, disconnect deletes the token, and the tool's behaviour when disconnected.

**8B (optional, feature flag):** Drive `drive.file` import into the Phase 4 pipeline, and Gmail readonly plus drafts only. This is a separate commit.

**Reminder:** in Testing mode, refresh tokens expire after 7 days, so re-authorise before any demo.

**Commit:** `phase-8: google oauth and calendar integration`

---

## Phase 9: Analytics and Streamlit dashboard

**Build**
- `GET /analytics/summary`: completions per day (30 days) and per week, goal progress, overdue and due-soon counts, the streak, and memory-state counts, all using SQL aggregates scoped to the user.
- `frontend/api_client.py` (a typed client) and `frontend/streamlit_app.py` with pages for **Chat, Documents, Goals & Tasks, Dashboard, Memory, Integrations**.
- Login: Firebase REST email and password sign-in from Python (the simplest option, logged in DECISIONS). The token is kept in `st.session_state` and refreshed when it expires.
- Plotly charts: completions over time, goal progress bars and a memory-state donut.
- Friendly handling when the API is down, on a 401 (which sends you back to login) and on a 413.

**How you verify:** using **only the UI**, sign up, chat and see memory working, upload a file, create a goal, generate a plan, and watch the dashboard update.

**Commit:** `phase-9: analytics endpoint and streamlit dashboard`

---

## Phase 10: Hardening, deployment, docs, CI

**Build**
- CORS limited to `FRONTEND_ORIGIN`; `slowapi` rate limits; a per-user daily LLM budget; body and upload limits; security headers; no stack traces in production; JSON logs with request IDs and no PII; `pip-audit`; and a `/health/ready` check (DB and Chroma).
- A multi-stage production `Dockerfile` and separate dev and prod compose files.
- Render: an API service, a Streamlit service, paid Postgres, a persistent disk for Chroma, a cron job calling the lifecycle endpoint, and the deploy URL added to Firebase and Google.
- Docs: `README.md`, `ARCHITECTURE.md`, `API.md`, `DEPLOYMENT.md`, `PRIVACY.md`, `TESTING.md`, an up-to-date `DECISIONS.md`, and a QA checklist for the §10 demo script.
- GitHub Actions: ruff and pytest (Postgres service, temp Chroma, fake LLM, `live` tests excluded).

**Acceptance:** the HTTPS cloud URL works end to end; a fresh clone plus `.env` plus `docker compose up` works; CI is green.

**Commit:** `phase-10: hardening, deployment, docs, ci`

---

## Phase 11 (optional): Knowledge graph

This happens only after Phase 10 passes and only if there's a clear need. It would use Graphiti or Cognee behind a feature flag and a single `graph_service.py`.

---

## Standards applied in every phase

- **Isolation:** every SQL query, Chroma filter and Mem0 call is scoped by `user_id`. Tools get `user_id` from verified state only, and foreign IDs return 404. Each data type has a two-user test.
- **Time:** stored in UTC and timezone-aware; "today" and "this week" use the per-user timezone.
- **Errors:** the envelope `{"error": {"code", "message"}}`, with no internal details leaked.
- **Tests:** they run without any real API key (fake LLM and embedder). `live` tests are opt-in.
- **Secrets:** env vars only, and `.env.example` is kept current.
- **Git:** at least one `phase-N:` commit per phase, and `.env`, `chroma_data/` and credentials are never committed.

## Open housekeeping items

- ~~Prerequisites guide deletion~~: restored and kept, as you asked.
- The handoff mentions `docs/1_How_The_Pieces_Build_PersonaOS.md`, which isn't in the repo. The plan doesn't depend on it.
