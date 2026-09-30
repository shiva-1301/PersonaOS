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

## Phase 2: database and authentication

| Date | Decision | Reason |
|---|---|---|
| 2026-09-30 | **Firebase ID tokens are verified with PyJWT + `cryptography` against Google's x509 certs**, not with `firebase-admin`. `app/auth/jwt_verify.py` performs every check the Firebase docs list for third-party JWT libraries: `alg=RS256`, `kid` from `…/x509/securetoken@system.gserviceaccount.com`, `aud` = project ID, `iss` = `https://securetoken.google.com/<project>`, `exp`/`iat`/`auth_time` present and valid, non-empty `sub`. Certs are cached for the `Cache-Control: max-age` the endpoint returns, and refetched early when an unknown `kid` appears (key rotation). | The handoff allows either option. This one needs no service account and doesn't pull in `firebase-admin`'s gRPC and Cloud SDK dependencies. Verification sits behind a `TokenVerifier.verify(token) -> Claims` interface, so Auth0 can be swapped in. |
| 2026-09-30 | Clock-skew leeway of **30 s** on `exp`/`iat`/`auth_time`. | Tolerates small drift between Docker/WSL clocks and Google's. |
| 2026-09-30 | Token revocation is **not** checked (tokens remain valid until they expire, at most 1 h). | Revocation checks need the Admin SDK with a service account and an extra network call per request. This can be revisited in Phase 10. |
| 2026-09-30 | **Fake verifier** (`AUTH_PROVIDER=fake`) accepts only tokens matching `test-[a-z0-9-]{1,50}` and uses the token as the uid. It's refused both by `Settings` (production + fake is a validation error) and by `build_verifier` (only allowed for `APP_ENV` dev/test). | CI and isolation tests don't depend on Firebase. The double guard makes it impossible to enable in production. |
| 2026-09-30 | Expired, wrong-audience, wrong-issuer, forged and rotated-key tests use **locally minted RS256 tokens** with a self-signed cert injected into `CertCache`. These run through the real `FirebaseVerifier`, and one runs end to end through `/me`. | Tests the real verification code without network access or real Firebase users. |
| 2026-09-30 | `get_current_user` does a **find-or-create keyed by `auth_uid`**. A race on first login is handled by catching `IntegrityError` and re-reading. The email is refreshed from the token if it changed. | Handoff Phase 2 §6. |
| 2026-09-30 | 401 responses carry `WWW-Authenticate: Bearer` and the standard error envelope. Messages are generic ("Missing bearer token", "Invalid token", "Token expired"). | Follows RFC 6750, without leaking verification internals. |
| 2026-09-30 | **`google_tokens`** replaces `calendar_tokens`: one row per user, with a `scopes` text column and a `needs_reconnect` flag. | Drive/Gmail may follow Calendar (Phase 8B) without a schema change, and `needs_reconnect` supports `invalid_grant` handling. |
| 2026-09-30 | Schema additions beyond Build Spec §4: `users.timezone` (default `UTC`); `chat_messages.user_id` (denormalised owner, so every table can be filtered by `user_id`); `updated_at` on sessions, goals, tasks, documents and google_tokens; `tasks.est_minutes` (for the planner); `documents.size_bytes`, `documents.source` (`upload`/`drive`) and `documents.error` (failure reason). | These are needed by later phases (handoff §7 timezone, Phase 4 failure reason, Phase 5 planner, Phase 8B Drive). Adding them now avoids early migrations. |
| 2026-09-30 | Status and enum fields are **`VARCHAR` + named `CHECK` constraints**, not native Postgres enums. | Adding a value later is a simple migration, whereas altering a native enum type is awkward. |
| 2026-09-30 | `tasks.goal_id` uses **`ON DELETE SET NULL`**. Everything else cascades from `users`. | Deleting a goal shouldn't silently delete the user's tasks. Deleting a user removes everything they own. |
| 2026-09-30 | `memory_meta.mem0_id` is `VARCHAR(64)` and the primary key. | Mem0 IDs are UUID strings. The exact format is re-checked in Phase 3. |
| 2026-09-30 | A deterministic **constraint naming convention** on `MetaData`. | Keeps Alembic migrations stable and diffable. |
| 2026-09-30 | **Migrations run on container start** (`alembic upgrade head && exec uvicorn …`). The host-side command is `alembic upgrade head`. | Idempotent, and verified: across two starts the upgrade ran only once. |
| 2026-09-30 | `alembic/env.py` takes its URL from app `Settings`, unless a URL is injected programmatically (tests). `alembic.ini` contains no URL. | A single source of truth, and no credentials in files. |
| 2026-09-30 | If `DATABASE_URL` is blank, it's **built from `POSTGRES_*`** with `urllib.parse.quote(safe="")`. Compose sets `POSTGRES_HOST=postgres` and clears `DATABASE_URL` for the container. | Host and container share one `.env`. Passwords with `@ / : % space` work, and `quote_plus` was rejected because SQLAlchemy doesn't decode `+` back to a space. |
| 2026-09-30 | **Default host is `127.0.0.1`, not `localhost`** (for both Postgres and Ollama). | On this Windows machine `localhost` tries IPv6 `::1` first, which hung until timeout because Postgres is published on 127.0.0.1 only. |
| 2026-09-30 | **Test database:** a dedicated `personaos_test` (plus `personaos_migrations_test`) on the compose Postgres, created automatically. It's migrated once per session with Alembic, and every table is truncated after each DB test. `TEST_DATABASE_URL` overrides it. If Postgres is down, DB tests **skip** with a clear message; with `PERSONAOS_REQUIRE_DB=1` (to be set in CI) they **fail** instead. | Tests never touch the dev database, and CI can't silently skip them. |
| 2026-09-30 | A migration test checks `upgrade head → compare_metadata == [] → downgrade base → upgrade head` on a fresh database. | Guarantees the models and migrations never drift apart, and that downgrade works. |
| 2026-09-30 | Pinned versions: `SQLAlchemy 2.1.1`, `alembic 1.20.0`, `psycopg[binary] 3.3.6`, `PyJWT[crypto] 2.15.1`, `python-dotenv 1.2.3` (now explicit). | Latest at build time, and `pip check` is clean. |
| 2026-09-30 | **`cryptography` pinned to 49.0.0, not the latest 50.0.2.** | **Windows Smart App Control is On** on the dev machine and blocks the native `_rust` DLL in `cryptography` 50.0.2 ("An Application Control policy has blocked this file"). 49.0.0 (and 48, 47, 46.0.3) load fine. It's probably reputation-based blocking of a very new binary. See the risk below. |
| 2026-09-30 | `scripts/get_id_token.py` gets a real Firebase ID token through the Identity Toolkit REST endpoint `accounts:signInWithPassword`, using `FIREBASE_WEB_API_KEY`. The password is read with a hidden prompt and the token printed to stdout. | Used for the manual `/me` check now, and it's the same approach the Streamlit login will use in Phase 9. |

### Risk noted: Smart App Control
Smart App Control can block other brand-new native wheels on the host, such as Chroma's dependencies (`onnxruntime`, `tokenizers`) and `grpcio` in later phases. Mitigations, in order:
1. Pin the newest version that loads.
2. Run that part of the test suite inside Docker (Linux, unaffected).
3. As a last resort, the owner turns Smart App Control off. This is **irreversible** without reinstalling Windows, so it's not recommended.

The container images aren't affected.
