# PersonaOS

**One Identity. One Memory. Infinite Intelligence.**

PersonaOS is a personal AI life-management assistant. It has long-term memory with a forgetting curve, search over your own documents (RAG), goal and task tracking with study-plan generation, and an agent that can act on your behalf. It's built with FastAPI, PostgreSQL, ChromaDB, Mem0, LangGraph and Streamlit.

> Status: **Phase 9 (web UI and dashboard)**. What is stored and how to delete it: [docs/PRIVACY.md](docs/PRIVACY.md). See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for progress and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.

## Quick start (Windows / PowerShell)

Prerequisites: Python 3.12, Docker Desktop (WSL 2), and [Ollama](https://ollama.com) running on the host with `ollama pull nomic-embed-text` and `ollama pull qwen2.5:7b`.

```powershell
# 1. Python environment (for tests and linting)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

# 2. Configuration
Copy-Item .env.example .env
# Edit .env and set POSTGRES_PASSWORD (any strong local password)

# 3. Run the stack (API + Postgres + web UI)
.\scripts\up.ps1            # or: docker compose up --build -d
curl.exe http://localhost:8000/health    # -> {"status":"ok"}
# Open the app: http://localhost:8501  (sign up / sign in with Firebase email + password)

# 4. Tests and lint
.\scripts\test.ps1
.\scripts\lint.ps1

# 5. Stop (data volumes are kept)
.\scripts\down.ps1
```

Database migrations run automatically when the `api` container starts. From the host: `alembic upgrade head` (or `alembic downgrade base`).
Tests need Postgres running (`docker compose up -d postgres`). They use a separate `personaos_test` database, which they create themselves.

### The web app

Open http://localhost:8501 after `.\scripts\up.ps1`. Create an account or sign in (Firebase email and password; `FIREBASE_WEB_API_KEY` must be in `.env`). The pages are:

- **Chat:** streamed replies with tools, memories used and sources. Proposed calendar events get Add / Don't add buttons.
- **Documents:** upload PDF, DOCX or TXT (up to 10 MB), summaries and search.
- **Goals & Tasks:** goals with progress, study-plan generation, and tasks you can tick off.
- **Dashboard:** your streak, completions per day or week, goal progress and memory states.
- **Memory:** what is remembered. Forget one memory, or delete everything (optionally your login too).
- **Integrations:** connect Google Calendar, confirm proposed events, see the next 7 days.

At first sign-in your timezone is taken from the browser; change it in the account menu (top right).

Run the UI outside Docker (the API still on port 8000; it runs from `frontend/` so the theme applies):
```powershell
.\scripts\ui.ps1
```

The look ("Aura": warm paper, a soft aura, serif headlines, pill buttons) comes from `frontend/.streamlit/config.toml` and `frontend/style.py`. Fonts are self-hosted in `frontend/static/fonts`.

The dashboard's data is also available from the API:
```powershell
Invoke-RestMethod "http://localhost:8000/analytics/summary?days=30&weeks=12" -Headers $h
```

### Live tests and model checks

```powershell
.\scripts\test.ps1 -m live -s                          # real Gemini + Ollama, fake data only
.venv\Scripts\python.exe scripts\smoke_tool_calling.py  # compare tool calling across models
# Full Docker memory check: store -> recall -> `docker compose restart api` -> recall -> user B sees nothing
.venv\Scripts\python.exe scripts\verify_memory_docker.py
# Full Docker document check on the files in test_files\ (git-ignored): ingest, retrieval,
# chat citations, user isolation, restart persistence, summary, delete -> 0 vectors
.venv\Scripts\python.exe scripts\verify_documents_docker.py
# Goals, tasks and a real study plan: window, weekly budget, preferences, progress, isolation
.venv\Scripts\python.exe scripts\verify_planner_docker.py
# The agent with real tools: goals, tasks, notes, plans, prompt injection, isolation, streaming
.venv\Scripts\python.exe scripts\verify_agent_docker.py
# Memory decay, supersession, deleting one memory, and "delete all my data" (every store)
.venv\Scripts\python.exe scripts\verify_lifecycle_docker.py
# Google Calendar with your real Google test account (opens the consent page in your browser)
.venv\Scripts\python.exe scripts\google_calendar_check.py --email <firebase-test-user-email> --timezone Asia/Kolkata
```

Memory extraction runs in the background after each reply. `POST /chat` returns `memory_status: "pending"`; `GET /chat/sessions/{id}` shows `done` (or `failed`) on that message once the memory is available (a few seconds when models are warm).

### Calling the API as a real user

```powershell
# Firebase test user -> ID token (password prompt is hidden). Needs FIREBASE_WEB_API_KEY in .env.
$t = .venv\Scripts\python.exe scripts\get_id_token.py --email test-a@example.com
curl.exe -H "Authorization: Bearer $t" http://localhost:8000/me

# Chat (first call starts a session; pass session_id to continue it)
$body = @{ message = "I study best after 6 pm." } | ConvertTo-Json
Invoke-RestMethod -Method Post http://localhost:8000/chat -Headers @{Authorization="Bearer $t"} -ContentType "application/json" -Body $body

# Documents (PDF/DOCX/TXT, max 10 MB): upload, then poll until status is "ready"
curl.exe -H "Authorization: Bearer $t" -F "file=@test_files\ML_Notes_2_Decision_Trees.docx" http://localhost:8000/documents
curl.exe -H "Authorization: Bearer $t" http://localhost:8000/documents
curl.exe -H "Authorization: Bearer $t" "http://localhost:8000/documents/search?q=entropy"

# Goals, tasks and study plans (set your timezone first: "today"/"this week" use it)
$h = @{Authorization="Bearer $t"}
Invoke-RestMethod -Method Patch http://localhost:8000/me -Headers $h -ContentType application/json -Body '{"timezone":"Asia/Kolkata"}'
$goal = Invoke-RestMethod -Method Post http://localhost:8000/goals -Headers $h -ContentType application/json -Body '{"title":"Finish ML course","target_date":"2026-11-30"}'
Invoke-RestMethod -Method Post "http://localhost:8000/goals/$($goal.id)/plan" -Headers $h -ContentType application/json -Body '{"hours_per_week":6}'
Invoke-RestMethod "http://localhost:8000/tasks?due=this_week" -Headers $h
```

Chat answers cite the documents they used in `sources`. Document text is always passed to the model as clearly delimited, untrusted data.

### The agent

Chat runs a LangGraph agent that can use tools on your behalf: create and list goals, add and update tasks, list this week's tasks, search and summarise your notes, generate study plans, and remember things you ask it to. `tools_used` in the response shows what it did. It can't delete anything, and it only ever acts on your own data. A clear request such as "Make me a study plan for my ML course goal, 6 hours a week" is planned in code, so the local model can't skip it.

```powershell
# Ask in plain language; the agent picks the tools
Invoke-RestMethod -Method Post http://localhost:8000/chat -Headers $h -ContentType application/json -Body '{"message":"Add a goal to finish my ML course by 30 Nov"}'

# Streaming (Server-Sent Events): token / reset / tool / done / error events
curl.exe -N -H "Authorization: Bearer $t" -H "Content-Type: application/json" -d '{\"message\":\"What do my notes say about entropy?\"}' http://localhost:8000/chat/stream
```

### Google Calendar

Optional: set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `TOKEN_ENCRYPTION_KEY` in `.env` (see `.env.example`). The assistant can read your upcoming events and **propose** new ones. An event is created only after you confirm, either by replying "yes" as your next message in the same chat (pass its `session_id`) or with `POST /integrations/google/proposals/{id}/confirm`. The "yes" is checked in code, not by the model. It never edits or deletes events. Times are read in your profile's timezone (`PATCH /me {"timezone": "Asia/Kolkata"}`).

```powershell
$url = (Invoke-RestMethod http://localhost:8000/integrations/google/start -Headers $h).authorization_url
Start-Process $url                                                   # consent in your browser
Invoke-RestMethod http://localhost:8000/integrations/google/status -Headers $h
Invoke-RestMethod "http://localhost:8000/integrations/google/events?days=7" -Headers $h
Invoke-RestMethod -Method Delete http://localhost:8000/integrations/google -Headers $h   # disconnect
```

In Google's Testing mode the connection expires after 7 days; the status shows `needs_reconnect` and you simply connect again.

### Memory and privacy

Memories fade when unused (active → stale → archived), get stronger when used, and are marked superseded when you state something that replaces them. See [docs/PRIVACY.md](docs/PRIVACY.md).

```powershell
Invoke-RestMethod http://localhost:8000/memory -Headers $h            # all memories with state
Invoke-RestMethod http://localhost:8000/memory/health -Headers $h     # counts by state, last 7 days
Invoke-RestMethod -Method Delete "http://localhost:8000/memory/<id>" -Headers $h
# Delete EVERYTHING stored about you (irreversible):
Invoke-RestMethod -Method Delete http://localhost:8000/me/data -Headers $h -ContentType application/json -Body '{"confirm":"DELETE MY DATA"}'

# Nightly lifecycle job (needs CRON_SECRET in .env; schedule it with Task Scheduler or cloud cron)
curl.exe -X POST -H "X-Cron-Secret: <your CRON_SECRET>" http://localhost:8000/internal/jobs/memory-lifecycle
```

If PowerShell blocks the scripts, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

API docs (dev only): http://localhost:8000/docs

## Layout

```
app/        FastAPI app (config, routers, services, agent, jobs, db, auth)
frontend/   Streamlit UI (api_client.py, firebase_auth.py, charts.py, views/)
tests/      pytest suite (no real API keys needed)
scripts/    PowerShell helpers
docs/       Spec, plan, decisions
```
