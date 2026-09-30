# Decisions Log

Each entry records the date, the decision and the reason. Newer entries go at the bottom of each section.

## Locked project decisions (D1 to D6)

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-09-30 | **Chat/agent LLM: Gemini Flash via Google AI Studio free tier.** A Flash model, not Pro (Pro isn't on the free tier). LLM calls retry with exponential backoff on HTTP 429. Development uses **fake data only**. | Free and has good tool calling. Free-tier prompts may be used by Google, hence fake data. The exact model name is confirmed in Phase 3. |
| D1a | 2026-09-30 | In Phase 3, the tool-calling smoke test runs against **both** Gemini Flash and a local ~8B Ollama model (e.g. `llama3.1:8b` / `qwen2.5:7b`, with names checked against the Ollama library). The results and a recommendation are recorded here. | Hardware: 16 GB RAM with an RTX 4050 (6 GB VRAM), so an 8B model at Q4 fits. Weak tool calling would hurt Phase 6. |
| D2 | 2026-09-30 | **Embeddings: Ollama `nomic-embed-text`**, running natively on the Windows host. The API container reaches it at `http://host.docker.internal:11434`. *(Changed from the proposed Gemini default.)* | Keeps documents and memories off the Gemini free tier, and embedding runs fine locally. **Fixed once chosen:** changing the model means re-embedding every stored vector. |
| D3 | 2026-09-30 | **Auth: Firebase Auth**, with Email/Password and Google sign-in only. No phone/SMS and no Blaze plan. | Free Spark plan, and simple ID-token verification in FastAPI. |
| D4 | 2026-09-30 | **Frontend: Streamlit** for v1. | Fastest route to a working UI. React only after all Must requirements pass. |
| D5 | 2026-09-30 | **Synchronous SQLAlchemy 2.x**, run in FastAPI's threadpool. | Simpler, and the Mem0 and Chroma clients are sync anyway. |
| D6 | 2026-09-30 | **Chroma: embedded persistent client inside the `api` container**, stored on the `chroma_data` named volume at `/data/chroma`. | Fewer services. Revisit only if we need to scale. |
| – | 2026-09-30 | Providers are **swappable by config only** (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, plus model env vars). | This is a hard requirement from the Build Spec. |

## Process and repository

| Date | Decision | Reason |
|---|---|---|
| 2026-09-30 | **Phase numbering follows `docs/3_PersonaOS_Continuation_Handoff.md`** (Phase 1 = app scaffold). The Build Spec's numbering is off by one. | Phase 0 (environment only) was already done. The handoff doc says its numbering is authoritative. |
| 2026-09-30 | **Branch is `master`**, not `main` as the handoff says. All phase commits go to `master`, and CI (Phase 10) triggers on `master`. | Matches the existing repo and remote. |
| 2026-09-30 | `docs/PersonaOS_Prerequisites_Setup_Guide.md` is kept (restored, not deleted). | Owner's instruction. |
| 2026-09-30 | Plan tracked in `docs/IMPLEMENTATION_PLAN.md`, with a gate: the next phase starts only after the owner verifies the current one. | Owner's instruction. |

## Phase 1: scaffold

| Date | Decision | Reason |
|---|---|---|
| 2026-09-30 | Pinned runtime deps: `fastapi 0.142.2`, `uvicorn[standard] 0.54.0`, `pydantic 2.13.5`, `pydantic-settings 2.15.0`, `python-json-logger 4.2.0`. Dev: `pytest 9.1.1`, `pytest-asyncio 1.4.0`, `httpx2 2.13.1`, `ruff 0.16.9`. `pip check` is clean. | Latest versions at build time, verified together. Only top-level packages are pinned for now; a full lock file can come in Phase 10. |
| 2026-09-30 | **`httpx2` instead of `httpx`** for tests. | Starlette 1.7's `TestClient` deprecates `httpx` in favour of `httpx2`, and using `httpx2` removes the warning. |
| 2026-09-30 | The app is built by a **factory** (`uvicorn app.main:create_app --factory`), with no module-level `app`. | Tests can build the app from explicit settings without reading a developer's `.env`. |
| 2026-09-30 | `env_ignore_empty=True` in settings, so blank `.env` entries count as unset. | `.env.example` leaves secrets blank, and blanks must not override defaults with `""`. |
| 2026-09-30 | Startup fails if `APP_ENV=production` with `AUTH_PROVIDER=fake`. This is enforced in `Settings` now, ahead of Phase 2. | Makes the unsafe combination impossible from day one. |
| 2026-09-30 | Error envelope `{"error": {"code", "message"}}` for HTTP errors, validation errors and unhandled exceptions (500 without internals). Added now instead of Phase 2. | It's cross-cutting, and cheap to add before any routers exist. |
| 2026-09-30 | Logging: a JSON formatter (`python-json-logger`), or plain text with `LOG_FORMAT=text`, with `request_id` on every record through a contextvar. Request-ID middleware reuses a safe incoming `X-Request-ID` (`[A-Za-z0-9._-]{1,64}`) or generates one, and echoes it in the response. | Observability standard from the handoff §7. Unsafe incoming IDs are replaced to avoid log injection. |
| 2026-09-30 | `/docs` (Swagger) is disabled when `APP_ENV=production`. | Less exposed surface in production. |
| 2026-09-30 | **Postgres image `postgres:17`**, with data on the `pg_data` volume at `/var/lib/postgresql/data`, published on `127.0.0.1` only (port set by `POSTGRES_HOST_PORT`, default 5432). | Postgres 18 changed its data-directory layout in the official image, and 17 is mature and fully supported. Binding to localhost avoids exposing the DB on the LAN. |
| 2026-09-30 | `POSTGRES_PASSWORD` is **required** by compose (`${POSTGRES_PASSWORD:?…}`), so there's no default password. | No secrets in Git. Compose fails fast with a clear message. |
| 2026-09-30 | Compose overrides `DATABASE_URL`, `CHROMA_PATH` and `OLLAMA_BASE_URL` for the container. Everything else comes from `.env` (`env_file` with `required: false`). | The same `.env` then works for both host runs and container runs. |
| 2026-09-30 | DB driver planned as **psycopg 3** (`postgresql+psycopg://`). It's installed in Phase 2. | The current SQLAlchemy-recommended Postgres driver. |
| 2026-09-30 | Helper scripts are **PowerShell** (`scripts/test.ps1`, `lint.ps1`, `up.ps1`, `down.ps1`) rather than a Makefile. | The developer is on Windows. |
