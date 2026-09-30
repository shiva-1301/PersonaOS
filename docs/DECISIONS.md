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

## Phase 3: LLM factory, Mem0 memory, plain chat

### D1a: tool-calling smoke test (`scripts/smoke_tool_calling.py`, run 2026-10-01)
Four checks, repeated per model: add one task with the correct ISO due date ("tomorrow"), list tasks, answer chit-chat **without** a tool, and add two tasks in one turn.

| Model | Result | Median latency | Notes |
|---|---|---|---|
| `gemini-2.5-flash` | 0/8 | – | Listed by the API but returns **404 NOT_FOUND** for this key (not offered to new projects). |
| **`gemini-3.5-flash`** | **8/8** | **1.8 s** | Occasional **503 "high demand"** on the free tier, which the backoff handles. |
| `gemini-3.8-flash` | 5/8 | 9.6 s | 3 × 503 "high demand", and slow. |
| `qwen2.5:7b` (Ollama) | 7/8, then **12/12** on rerun | 1.8 to 3.0 s | The one failure was an Ollama server crash (`0xc0000409`) while loading the model, not a tool-calling error. |
| `llama3.1:8b` (Ollama) | 8/8 | 5.3 s | Correct but slower on a 6 GB GPU. |

**Recommendation:**
- **Chat/agent: `gemini-3.5-flash`** (the default for `LLM_PROVIDER=gemini`). It's the fastest and most accurate.
- **Local fallback: `qwen2.5:7b`** (the default for `LLM_PROVIDER=ollama`). It's equally accurate after the rerun and fast. `llama3.1:8b` works but is about 3× slower.

Switching is config-only (`LLM_PROVIDER`, `LLM_MODEL`).

### Mem0 2.2.1: verified behaviour (from the installed source, not older docs)
| Date | Finding / decision | Consequence |
|---|---|---|
| 2026-10-01 | `Memory.add()` runs an **additive, ADD-only** pipeline: one LLM extraction call per turn. It never emits `UPDATE`/`DELETE` events. | **Phase 7 supersession must come from our own contradiction check.** It can't rely on Mem0 UPDATE events, as the handoff assumed. `save_turn` still handles UPDATE/DELETE defensively in case of future versions. |
| 2026-10-01 | `search()` takes `filters={"user_id": …}` and `top_k`. Top-level `user_id` is rejected. Scores are `1/(1+L2 distance)`. | Implemented that way. Every call passes `filters={"user_id": str(users.id)}`, and results are re-checked against the user as defence in depth. |
| 2026-10-01 | Mem0 extracts from **both** user and assistant messages. | `custom_instructions` restrict extraction to facts the **user** states about themselves, and forbid storing secrets. |
| 2026-10-01 | Mem0 keeps a **SQLite history DB, including recent messages**, at `~/.mem0/history.db` by default. | Moved to `<CHROMA_PATH>/mem0_history.db` (on the volume), with `MEM0_DIR=<CHROMA_PATH>/.mem0`. **Phase 7 `DELETE /me/data` must purge it too.** |
| 2026-10-01 | Mem0 and Chroma both send **usage telemetry** by default (PostHog). | Disabled: `MEM0_TELEMETRY=False` (set before `mem0` is imported, and in the Dockerfile), and the Chroma client is built with `anonymized_telemetry=False`, plus `ANONYMIZED_TELEMETRY=False`. |
| 2026-10-01 | Mem0's "langchain" provider needs the full `langchain` package and ignores `response_format`. | Instead there are **two small adapters** (`app/services/mem0_adapters.py`) registered under Mem0's "langchain" provider name. Mem0 therefore uses exactly the models from `app/services/llm.py`, with the same backoff. A test forbids OpenAI construction and proves Mem0 **never falls back to OpenAI**. |
| 2026-10-01 | spaCy isn't installed, so Mem0's entity linking and BM25 lemmatisation are skipped. Chroma has no keyword search, so there's a warning at startup. | Retrieval is semantic only. That's fine at our scale, and it avoids a spaCy download. |
| 2026-10-01 | Mem0 and document RAG share **one `chromadb.PersistentClient`** per path. Mem0's collection is `personaos_mem0__<provider>-<model>` (currently `personaos_mem0__ollama-nomic-embed-text`). | Changing the embedding model automatically uses a new collection instead of corrupting the old one. Re-embedding is still needed to keep old memories. |

### Memory extraction model
| Date | Decision | Reason |
|---|---|---|
| 2026-10-01 | New optional settings **`MEMORY_LLM_PROVIDER` / `MEMORY_LLM_MODEL`**. Blank means the same as the chat LLM. The memory model gets its own `MEMORY_OLLAMA_NUM_CTX` (default 16384, minimum 12288), `MEMORY_LLM_TIMEOUT_SECONDS` (180) and temperature 0. | Mem0's extraction prompt is **~33.6k chars / ~8.1k tokens every turn**, which is larger than the default 8k Ollama window. |
| 2026-10-01 | **Recommended setting for this machine: `MEMORY_LLM_PROVIDER=ollama` (`qwen2.5:7b`), with chat staying on Gemini.** | Extraction benchmark: **qwen2.5:7b** extracted exactly the right facts ("User studies best after 6 pm"; the course and deadline plus "6 hours per week"), and nothing from chit-chat. It took 28 s on the first call (model load), then 1 to 8 s. **gemini-3.5-flash** did not complete a single extraction: the first live test got **499 CANCELLED** at the 60 s timeout, a 20-minute benchmark returned nothing, and the quota then ran out (see below). Running extraction locally matches D2's intent (keep memories off the free tier), saves 1 of the 2 Gemini requests per chat turn, and runs in the background, so the extra seconds don't affect reply time. |
| 2026-10-01 | The memory model runs in **JSON mode** (Ollama `format="json"`, Gemini `response_mime_type="application/json"`). | Without it, qwen2.5:7b once returned malformed JSON, and Mem0 logged a parse error and **silently dropped that turn's memories**. With JSON mode, the live runs had no parse errors. |

### Gemini free-tier quota (critical finding)
| Date | Finding | Consequence |
|---|---|---|
| 2026-10-01 | A 429 response showed the free tier for **`gemini-3.5-flash` is 20 requests per day, per project, per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20). The limit resets at midnight Pacific. Today's smoke tests and the first live test used it all. | At 2 requests per chat turn (reply + extraction) that's about 10 turns a day. The Phase 6 agent makes several calls per turn. **That's not workable as the only LLM for development or the demo.** The code default stays Gemini (D1) and **nothing was switched silently**; the choice goes back to the owner in the Phase 3 report. The options: (a) local `qwen2.5:7b` for chat (12/12 tool calls, 2 to 3 s, unlimited, private); (b) a Flash-Lite model, each of which has its **own** daily quota (limits visible in AI Studio > Rate limits): `gemini-3.1-flash-lite` scored 4/4 on tool calls at 3.3 s, and `gemini-3.5-flash-lite` 3/4 at 0.8 s (it ignores `temperature`); (c) enable billing on the Gemini project. |
| 2026-10-01 | Verified end to end (live test, real models): **all-local** (qwen2.5:7b chat and memory, nomic embeddings) passed in 159 s including model loads. **Gemini `gemini-3.1-flash-lite` chat + local memory** passed in 28 s. Inside Docker, memory survived an `api` container restart (`memories_used: 1` in a new session after the restart). | The acceptance criteria are met with the owner's hardware and free keys. |

### Privacy gap found for Phase 7
| Date | Finding | Consequence |
|---|---|---|
| 2026-10-01 | `Memory.delete_all(user_id=…)` removes vectors, but leaves Mem0's SQLite `messages` (raw conversation text, keyed by `session_scope='user_id=<id>'`) and `history` rows (keyed only by `memory_id`, with no user column). This was found with a test user in the container and cleaned up by hand. | `DELETE /me/data` and `DELETE /memory/{id}` (Phase 7) must: collect the user's memory IDs first, delete the vectors, delete `history` rows by those IDs, and delete `messages` by `session_scope`. A test must assert zero rows remain. |

### Other Phase 3 decisions
| Date | Decision | Reason |
|---|---|---|
| 2026-10-01 | **Retry policy** (`invoke_with_backoff`): up to `LLM_RATE_LIMIT_ATTEMPTS` (4) tries on `ModelRateLimitError` (429), `ModelAPIError` (5xx, e.g. "high demand") and `ModelConnectionError`. The server's `retry_delay`/`retryDelay` is honoured when present; otherwise the wait is exponential (2^n s + jitter), capped at 60 s. Gemini SDK retries are off (`max_retries=1`), because the SDK ignores the server delay. Bad-request, auth, not-found and timeout errors are not retried. | D1 asked for 429 backoff. The smoke tests showed frequent 503s on the free tier, so those are retried too. |
| 2026-10-01 | Errors from `/chat`: 429 after retries gives **503** + `Retry-After: 60` (`service_unavailable`); other provider failures give **502** (`upstream_error`) with a generic message; a missing key gives 503. Nothing is persisted for a failed turn. | The user sees a clear message, and internals are never leaked. |
| 2026-10-01 | `save_turn` runs in FastAPI **BackgroundTasks** after the response, with its own DB session. Failures are logged (`Memory save failed`), never swallowed silently, and never break the reply. If `recall` fails (e.g. Ollama down), chat continues without memories and logs `Memory recall failed`. | Handoff Phase 3 §4. Extraction is the slow part, and replies shouldn't wait for it. |
| 2026-10-01 | `recall` (Phase 3 stub): fetch `k*3` from Mem0, join `memory_meta` for this user, drop `archived`/`superseded`, rank by `relevance × strength`, return top `k` (`MEMORY_RECALL_K`=5). No reinforcement yet (Phase 7). A Mem0 hit without a meta row is treated as active with strength 1.0. | Build Spec §5.2, with lifecycle stubbed as the handoff says. |
| 2026-10-01 | Message timestamps are set in Python (user message before the LLM call, reply after), not by Postgres `now()`. | `now()` is per-transaction, so both messages would otherwise tie and could be returned out of order. |
| 2026-10-01 | `nomic-embed-text` is wrapped to add its trained task prefixes (`search_query: ` / `search_document: `). | Better retrieval quality for that model. Other models are unaffected. |
| 2026-10-01 | Heavy services (models, Chroma, Mem0) are built **lazily** by `app/services/container.py`. | `/health` and `/me` work even when Ollama is down or a key is missing. Tests swap in fakes by assignment. |
| 2026-10-01 | **Offline fakes** (`app/services/fakes.py`): a bag-of-words hashing embedder, and a fake chat model that answers Mem0 extraction prompts with the user's lines and echoes the memories it was given. `LLM_PROVIDER`/`EMBEDDING_PROVIDER=fake` are refused in production. | CI never calls a real provider, and tests can assert exactly what reached the prompt. |
| 2026-10-01 | `pytest -m live` (`tests/test_live.py`) runs the acceptance scenario against the real `.env` providers. It's excluded by default. | Handoff Phase 3 §7. |
| 2026-10-01 | Default Ollama `num_ctx` for chat is **8192** (`OLLAMA_NUM_CTX`). | Enough for the system prompt, memories and 20 history messages. |
| 2026-10-01 | Pinned: `langchain-core 1.6.6`, `langchain-google-genai 4.4.0`, `langchain-ollama 1.1.0`, `mem0ai 2.2.1`, `chromadb 1.5.9`, `tzdata 2026.4`. No PyTorch. Transitive heavy dependencies are `onnxruntime`, `grpcio`, `kubernetes` (from chromadb) and `qdrant-client` (from mem0). All native modules load under Smart App Control. | Latest at build time, and `pip check` is clean. |
| 2026-10-01 | Removed `openai` from `LLM_PROVIDER`/`EMBEDDING_PROVIDER`, and `OPENAI_*` from config. | Not installed or tested, and advertising it would be misleading. Adding a provider is one builder function in `llm.py`. |

## Phase 3 fix: "User A's follow-up returns memories_used=0" (2026-10-01)

### Evidence gathered before any code change
- **Postgres ↔ Chroma:** all 8 `memory_meta` rows had a matching vector in `personaos_mem0__ollama-nomic-embed-text`, with the correct `user_id` in the vector metadata. There were 0 orphans in either direction.
- **Retrieval run directly inside the container:** for user A, the top hit for both study-time questions was "User studies best after 6 pm and prefers quiet evenings" (relevance 0.71 / 0.78). User B got only B's own memory. So persistence, the `user_id` filter and ranking were correct.
- **Timeline from `docker compose logs api` plus the chat_messages timestamps:**

  | Time | Event |
  |---|---|
  | 20:26:21 | A says "I study best after 6 pm…", reply at 20:26:35 |
  | 20:26:35 | A's new-session question; the reply came at 20:26:40, before any memory existed |
  | 20:26:48 | A's next question; recall ran immediately, while extraction was still running (the reply took until 20:27:23) |
  | 20:27:09 | The first turn's extraction finally inserted the memory (**48 s after the message**) |
  | 20:29:14 | After the restart: the reply was *"Given that you study best after 6 pm…"*, so the memory *was* used |

- **Ollama `server.log`:** qwen2.5:7b and nomic-embed-text were **evicted and reloaded every few seconds** ("model predicted to exceed available memory, evicting"). Chat requested `num_ctx=8192` and extraction `16384`, and Ollama must reload a model to change its context size. The embedder and qwen (4.8 to 5.2 GiB) also didn't fit together in the 4.7 GiB of free VRAM.

### Root cause (in plain words)
Memories were never lost or mixed between users. **They weren't ready yet.** Memory extraction runs in the background after the reply, and on this 6 GB GPU it took 25 to 48 s because Ollama kept swapping models in and out of VRAM. The follow-up questions were asked inside that window, so there was nothing to recall. There was also no way to *see* whether extraction had finished, so "not yet" looked the same as "broken".

### Fixes
| Decision | Reason |
|---|---|
| **`EMBEDDING_ON_CPU=true` (default):** Ollama embeddings run with `num_gpu=0`. | nomic-embed-text (137M) takes ~0.03 s per call on CPU and no longer competes with the chat model for VRAM. Measured: 0 evictions and 0 reloads during a full verification run. |
| When chat and extraction use the **same Ollama model**, chat uses the memory context size (`max(OLLAMA_NUM_CTX, MEMORY_OLLAMA_NUM_CTX)`, i.e. 16384). Different models keep their own. | One context size means no reload between chat and extraction. |
| New column **`chat_messages.memory_status`** (`pending` → `done` / `failed`) on user messages, with migration `cdf88b5411e1`. It's returned by `POST /chat` (`message_id`, `memory_status`) and `GET /chat/sessions/{id}`. The background task records the outcome and logs `extraction_ms`. | Extraction is asynchronous by design (replies shouldn't wait for it), so its completion must be observable. Tests and scripts poll it with a timeout instead of sleeping, and the Phase 9 UI can show "remembering…". The status update is scoped to the message's owner. |
| **Only the user's own words are sent to Mem0** (not the assistant reply), and the instructions now say questions and requests aren't facts. | The assistant's clarifying questions had produced invented memories such as "User plans to study for a certain duration per session", which crowd out real facts in recall. |
| **Measured after the fix (Docker, real models):** reply 3.8 s, and extraction done **4.2 s** after the reply when warm. After a cold `api` restart and model load: reply 16.1 s, extraction 13.6 s. | Before: 25 to 48 s extraction with constant reloads. |

### Test isolation fix
| Decision | Reason |
|---|---|
| An autouse fixture in `tests/conftest.py` deletes **every `Settings` field name and any `LLM_*`, `MEMORY_LLM_*`, `MEMORY_*`, `OLLAMA_*`, `GEMINI_*`, `EMBEDDING_*` variable** from the process environment for each test. Tests marked `live` opt out. The offline defaults now also pin `MEMORY_LLM_PROVIDER=fake`. | `Settings(_env_file=None)` still reads `os.environ`. With provider variables exported, several "offline" tests picked up the developer's config, and `test_memory_carries_across_sessions` **actually called the real Ollama** for extraction (MEMORY_LLM_PROVIDER wasn't pinned). Verified afterwards: 106/106 pass with hostile variables exported, and the Ollama log shows **0 requests** during the run. |

### Regression coverage
- `tests/test_memory_regression.py` (offline): A stores a memory (extraction polled until `done`); a new session recalls the same memory ID and it appears in the **injected system prompt**. Then every service object is discarded (the Chroma client is closed and the DB engine disposed) and rebuilt on the same Chroma directory and Postgres database, the offline equivalent of a restart. A recalls the same memory ID again, it's injected again, and B gets `memories_used == 0` with nothing from A in either B's prompt or B's recall. Also covered: `memory_status` done/failed, only user words sent for extraction, and status updates scoped to the owner.
- `scripts/verify_memory_docker.py` (real Docker, real models): uses a unique random fake fact per run, so earlier memories can't make it pass. It polls extraction, checks new-session recall both **via the API and via the app's recall inside the container**, runs `docker compose restart api`, repeats the checks, confirms B sees nothing, and checks the container uses `host.docker.internal` for Ollama. `--auth fake` (default) temporarily runs the api with `docker-compose.fake-auth.yml` and cleans up afterwards (Postgres, Mem0 vectors, Mem0 SQLite). `--auth firebase` uses the real test users.

## Phase 4: documents and RAG (2026-10-01)

| Decision | Reason |
|---|---|
| **Parsers:** `pypdf 6.19.0`, `python-docx 1.2.0` (with `lxml 6.1.3`), `charset-normalizer 3.5.2` for TXT encodings (UTF-8 and BOM are tried first), and `python-multipart 0.0.32` for uploads. All native modules load under Smart App Control. | Current versions, `pip check` is clean, and no PyTorch. |
| **Type allowlist = content sniffing plus extension.** `.pdf` needs `%PDF-` in the first 1024 bytes. `.docx` needs a zip containing `word/document.xml` and `[Content_Types].xml`. `.txt` must have no NUL bytes, must not look like PDF or zip, and must decode. Anything else, or any mismatch, gets **415**. | Handoff Phase 4 §1: never trust the extension alone. |
| **Safety limits:** a DOCX may expand to at most 100 MB (zip-bomb guard); PDFs are limited to 500 pages; encrypted PDFs are rejected; filenames are reduced to their basename with control characters stripped; nothing is ever executed. | Safe parsing. |
| **Size limit:** `MAX_UPLOAD_MB` (10). It's enforced twice: by middleware on `Content-Length` before the body is read, and by the router on the bytes it actually reads, which covers a missing or false Content-Length. Both return **413**. A proxy limit follows in Phase 10. | Handoff Phase 4 §1 and Phase 10. |
| **Raw uploads are not stored.** The bytes go from the request to the background task in memory (bounded by `MAX_UPLOAD_MB`) and are dropped after ingestion. | The handoff says not to keep uploads unless asked. That means re-ingesting needs a re-upload, which is fine for now. |
| **Background ingest**, with the status on the row: `processing` → `ready` (with `chunk_count`) or `failed` (with a short, user-safe `error`). Parse errors give specific reasons (e.g. "No extractable text … OCR is not supported"); unexpected errors give a generic message and are logged. If the row is deleted while ingesting, the vectors just written are removed (no orphans). | Handoff Phase 4 §3. |
| **Chunking:** character-based, `RAG_CHUNK_CHARS=3200` (≈800 tokens) with `RAG_CHUNK_OVERLAP_CHARS=400` (≈100 tokens). It splits at paragraph, then line, then sentence, then word boundaries in the last quarter of each window, and never starts a chunk mid-word. | The handoff allows tiktoken or an approximation. tiktoken would download its vocabulary at runtime and is OpenAI-specific, so it doesn't match nomic's tokenizer anyway. |
| **Chroma collection `personaos_documents__<embedding id>`**, using **cosine** distance and separate from Mem0's collection. Metadata is `{user_id, document_id, filename, chunk_index}`, and chunk ids are `<document_id>:<index>`, so re-ingesting replaces the chunks. | Build Spec §2. The embedding-model id in the name means a model change can't corrupt the collection. |
| **`retrieve(user_id, query, /, …)`:** `user_id` is a **required positional argument that must be a UUID** (a string or `None` raises `TypeError`). Every query and delete uses a `where` filter on `user_id`, and results are re-checked. | Handoff: "make it impossible to call without a user_id". |
| **Relevance gating in chat:** `RAG_MIN_RELEVANCE=0.55` and `RAG_RELATIVE_MARGIN=0.1` (drop chunks scoring more than 0.1 below the best hit). Calibrated with nomic on the owner's three notes and nine questions: the correct document's top hit scored 0.59–0.73; off-topic questions ("hello", "pasta recipe", "plan my week") scored at most 0.514, so 0.5 would have let them through. That's a small sample, so re-check the values if other kinds of documents behave differently. | Keeps unrelated notes out of everyday chat. |
| **Chat uses RAG now (plain chat, before the Phase 6 agent).** Retrieved chunks are injected as untrusted excerpts, and `POST /chat` returns `sources` (document_id, filename, chunk_index) as citations. If retrieval fails, chat continues without excerpts and logs it. | Demo script step 3 ("see a cited answer"). |
| **Prompt-injection guard:** excerpts are wrapped in `<<<UNTRUSTED_DOCUMENT_EXCERPT source="…" part=N>>> … <<<END_UNTRUSTED_DOCUMENT_EXCERPT>>>`. Any delimiter look-alike inside a document is replaced with `[removed delimiter]`, so a file can't close the block or open a forged one. The system prompt states excerpts are data, never instructions, and asks the model to cite the file. Summaries use the same wrapping. | Handoff Phase 4 §4. The Phase 6 test will also assert that no destructive tool call happens. |
| **`GET /documents/search?q=&k=`**: semantic search over the user's own chunks (503 if the embedder is down). | Makes retrieval directly testable and useful for the UI. |
| **Summaries (`POST /documents/{id}/summarize`):** map-reduce. Chunks are grouped up to `SUMMARY_GROUP_CHARS` (9000) per call, which needs fewer LLM calls than one per chunk. Map produces bullets per group; reduce combines them, recursively, and forces a single final call if nothing can merge. The result is stored in `documents.summary`. It returns 409 if the document isn't ready, 502/503 on LLM errors, and runs synchronously. | Handoff Phase 4 §3. Local qwen took 12.9 s for one note. |
| Deleting a document removes the **vectors first, then the row**, so a failure leaves a retryable state. Deleting a *user* cascades in Postgres but not in Chroma, so the verification cleanup deletes chunks by `user_id` explicitly. | **Phase 7 `DELETE /me/data` must do the same** (added to the Phase 7 checklist). |
| `test_files/` (the owner's sample notes) is git-ignored. | They're the owner's files; the tests generate their own PDF/DOCX/TXT in memory (`tests/doc_factory.py`). |

### Verification (Docker, real models: qwen2.5:7b and nomic-embed-text on CPU)
`scripts/verify_documents_docker.py` passed 27/27 checks with the owner's files:
- **Ingest:** TXT 4.4 s (cold embedder), DOCX 0.7 s, PDF 0.5 s, 1 chunk each. `Scanned_Notes_NoText.pdf` failed with "No extractable text … OCR is not supported".
- **Retrieval:** a real sentence from each document ranked that document first (relevance 0.68–0.80).
- **Chat:** "What do my notes say about <topic>?" cited the right file in all three cases and answered from the notes.
- **Isolation:** B got no search hits, 404 on A's documents, and no chat sources.
- **Persistence:** after `docker compose restart api`, all three documents were still found.
- **Summary:** generated and stored (12.9 s).
- **Delete:** 204, Chroma chunks 1 → 0, other documents intact, and a 404 afterwards.

### Performance note (not a bug)
Chat answers of several hundred tokens took 28–47 s on the local model. Measured on this laptop: qwen2.5:7b generates **~15 tokens/s at 16k context and ~17 at 8k**. It uses ~3.9–4.05 GB of the ~4.7 GB free VRAM, and Ollama keeps some layers on the CPU at any context size, so the context size isn't the bottleneck. Short answers take ~4 s. Options for the owner:
- keep answers concise (already in the prompt)
- stream replies (optional SSE in Phase 6)
- a smaller local chat model (e.g. qwen2.5:3b)
- a Gemini Flash-Lite model for chat (daily quota applies)
