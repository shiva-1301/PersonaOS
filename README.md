# PersonaOS

**One Identity. One Memory. Infinite Intelligence.**

PersonaOS is a personal AI life-management assistant. It has long-term memory with a forgetting curve, search over your own documents (RAG), goal and task tracking with study-plan generation, and an agent that can act on your behalf. It's built with FastAPI, PostgreSQL, ChromaDB, Mem0, LangGraph and Streamlit.

> Status: **Phase 1 (scaffold)**. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for progress and [docs/DECISIONS.md](docs/DECISIONS.md) for design decisions.

## Quick start (Windows / PowerShell)

Prerequisites: Python 3.12 and Docker Desktop (WSL 2).

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
