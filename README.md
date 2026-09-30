# PersonaOS

**One Identity. One Memory. Infinite Intelligence.**

PersonaOS is a personal AI life-management assistant. It has long-term memory with a forgetting curve, search over your own documents (RAG), goal and task tracking with study-plan generation, and an agent that can act on your behalf. It's built with FastAPI, PostgreSQL, ChromaDB, Mem0, LangGraph and Streamlit.

> Status: **Phase 5 (goals, tasks and study plans)**. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for progress and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.

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

# 3. Run the stack (API + Postgres)
.\scripts\up.ps1            # or: docker compose up --build -d
curl.exe http://localhost:8000/health    # -> {"status":"ok"}

# 4. Tests and lint
.\scripts\test.ps1
.\scripts\lint.ps1

# 5. Stop (data volumes are kept)
.\scripts\down.ps1
```

Database migrations run automatically when the `api` container starts. From the host: `alembic upgrade head` (or `alembic downgrade base`).
Tests need Postgres running (`docker compose up -d postgres`). They use a separate `personaos_test` database, which they create themselves.

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

If PowerShell blocks the scripts, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

API docs (dev only): http://localhost:8000/docs

## Layout

```
app/        FastAPI app (config, routers, services, agent, jobs, db, auth)
frontend/   Streamlit UI (Phase 9)
tests/      pytest suite (no real API keys needed)
scripts/    PowerShell helpers
docs/       Spec, plan, decisions
```
