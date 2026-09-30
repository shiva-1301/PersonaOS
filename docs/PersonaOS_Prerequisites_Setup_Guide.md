# PersonaOS: Prerequisites and Setup Guide

*Everything you must prepare by hand before your coding agent (Claude Code or similar) starts building. Package names and commands come from general knowledge of these tools; the agent must verify current versions against official docs. Dated 30 Sep 2026.*

---

## 0. How to use this guide

1. Do **Part A (Must-have now)** completely before starting the agent.
2. Do the **staged parts (B to E)** only when the build reaches that phase. Doing them early wastes time and can cost money.
3. Tick every box in **Part G (Readiness checklist)** before you say "start Phase 0".

| Part | What | Needed before |
|---|---|---|
| A | Tools on your computer, project folder, GitHub | Phase 0 |
| B | LLM and embeddings access | Phase 2 |
| C | Firebase (or Auth0) authentication | Phase 1 |
| D | Google Cloud (Calendar, Drive, Gmail) | Phase 7 |
| E | Render deployment | Phase 10 |
| F | Secrets and the `.env` file | Phase 0 (template), filled as you go |

---

## Part A: Tools and project home (needed for Phase 0)

### A1. Software to install

| Tool | Version | Why | Verify with |
|---|---|---|---|
| Python | 3.12 or newer | Backend language | `python --version` |
| Git | any recent | Version control, one commit per phase | `git --version` |
| Docker Desktop | recent | Runs PostgreSQL and the app locally | `docker --version` and `docker compose version` |
| Code editor (VS Code) | any | Review what the agent writes | opens fine |
| Coding agent (Claude Code or your choice) | current | Builds the project | logs in and runs |
| Ollama | current, **only if** you use local models | Local LLM and embeddings | `ollama --version` |
| Node.js | LTS, **only if** you later build the React frontend | Frontend tooling | `node --version` |

**Windows tips:** install Docker Desktop with the WSL 2 backend and restart. Use Git Bash, PowerShell or WSL for the commands below.
**Hardware:** the spec lists 8 GB RAM minimum, 16 GB+ recommended, RTX 3060 optional. Local models without a GPU will be slow.

### A2. Create the project folder and repository

```bash
mkdir personaos && cd personaos
git init
python -m venv .venv
# activate the virtual environment
#   macOS/Linux:  source .venv/bin/activate
#   Windows:      .venv\Scripts\activate
python -m pip install --upgrade pip
mkdir docs
```

1. Create an empty **GitHub repository** (private is fine).
2. Connect it: `git remote add origin <your-repo-url>`.
3. Create a `.gitignore` containing at least: `.env`, `.venv/`, `__pycache__/`, `chroma_data/`, `*.pem`, `*.json` service-account keys, `.DS_Store`.
4. Copy these into `docs/`:
   - `2_PersonaOS_Agent_Build_Spec.md`
   - `PersonaOS_Implementation_Document.md`
   - `PersonaOS_Abstract_and_Project_Specifications.docx`
   - `3_PersonaOS_Budget_Estimate.md` (optional)
5. First commit: `git add . && git commit -m "chore: initial docs"` and push.

### A3. Confirm Docker works

```bash
docker run --rm hello-world
```

If this fails, fix Docker before anything else. The agent's `docker-compose.yml` will rely on it for PostgreSQL.

### A4. Instructions to give the agent at the start

Paste something like this as your first message:

> Read `docs/2_PersonaOS_Agent_Build_Spec.md` and `docs/PersonaOS_Implementation_Document.md`. Work phase by phase. Do not start a phase until the previous phase's acceptance checks pass. After each phase run the tests, commit as `phase-N: summary`, and report. Verify library APIs against current official docs before coding. Never hardcode secrets; use `.env` and create `.env.example`. Ask me when you need a key, ID or manual step.

---

## Part B: LLM and embeddings (needed for Phase 2)

Pick **one** path to start. The spec makes providers swappable by config, so you can change later.

### Option 1: Gemini through Google AI Studio (easiest, no GPU)
1. Go to aistudio.google.com and sign in.
2. Click **Get API key** and create a key.
3. Save it as `GEMINI_API_KEY` (or `GOOGLE_API_KEY`, as the agent's library expects).
4. **Free tier warning:** Google may use free-tier prompts to improve its products (outside the EU, UK and Switzerland). Use **fake data only**, never real emails, files or personal details.
5. Expect rate limits on the free tier; multi-step agent turns make several LLM calls.

### Option 2: Ollama (free, local, works offline)
```bash
ollama pull llama3            # or mistral / gemma
ollama pull nomic-embed-text  # embedding model
ollama run llama3 "Say hello" # test it
```
- Keep Ollama running. Default address is `http://localhost:11434`.
- From inside Docker on Windows/macOS, the app reaches it at `http://host.docker.internal:11434`.
- Local models are usually weaker at tool calling; test before relying on them for the agent phase.

### Option 3: OpenAI or Anthropic API (paid, most reliable tool calling)
1. Create an account, add a small prepaid balance and generate a key.
2. **Immediately set a monthly spend limit and usage alerts** in the dashboard.
3. Save as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

### Embeddings
- Local: Ollama embedding model (above) or `sentence-transformers` (needs RAM).
- API: OpenAI `text-embedding-3-small` or a Gemini embedding model. Embedding ~100 documents costs a few rupees.
- **Warning:** if you switch the embedding model later, existing vectors become incompatible and must be re-embedded. Decide early.

### Mem0 note
Mem0 needs its own LLM and embedder settings, and may default to OpenAI-style settings. The agent must configure it to use your chosen provider through the `llm.py` factory. If you see calls to a provider you did not set up, that is the cause.

---

## Part C: Authentication (needed for Phase 1)

### Firebase (recommended)
1. Go to console.firebase.google.com and create a project.
2. **Build → Authentication → Get started.** Enable **Email/Password** and **Google** sign-in. Skip phone sign-in (billed per verification).
3. **Project settings → Your apps → Add web app.** Copy the config values: `apiKey`, `authDomain`, `projectId`, `appId`.
4. Note the **project ID**: the backend uses it to verify ID tokens.
5. Add **Authorized domains** (`localhost` is there by default; add your Render domain later).
6. Create two test users so you can test isolation between users.
7. Only if the agent asks for it: **Project settings → Service accounts → Generate new private key.** Store the JSON outside Git and point to it with an env variable.

### Auth0 (alternative)
Create a tenant, an API (audience) and a Single Page Application; note domain, audience and client ID. Free plan covers 25,000 MAU.

---

## Part D: Google Cloud for Calendar, Drive and Gmail (needed for Phase 7)

This is **separate** from Firebase login. Firebase proves who the user is; this grants access to their Google data.

### D1. Create a test Google account first
Make a dedicated Gmail account with fake emails, calendar events and Drive files. Use it for all development.

### D2. Project and APIs
1. Go to console.cloud.google.com and create a project (or reuse your Firebase project).
2. **APIs & Services → Library:** enable **Google Calendar API**, **Google Drive API**, **Gmail API**.

### D3. OAuth consent screen
1. **APIs & Services → OAuth consent screen** (Google Auth Platform).
2. User type **External**. Fill in app name, support email, developer email.
3. Publishing status: keep **Testing**.
4. Add scopes. Request incrementally as phases require:

| Feature | Scope | Tier |
|---|---|---|
| Calendar | `https://www.googleapis.com/auth/calendar.events` | Sensitive |
| Drive (picked/app files) | `https://www.googleapis.com/auth/drive.file` | Non-sensitive |
| Drive (read all, optional) | `https://www.googleapis.com/auth/drive.readonly` | Restricted |
| Gmail read (optional) | `https://www.googleapis.com/auth/gmail.readonly` | Restricted |
| Gmail draft/send (optional) | `gmail.compose`, `gmail.send` | Sensitive |

5. **Test users:** add your Gmail, teammates and evaluators (max 100).

### D4. OAuth client credentials
1. **Credentials → Create credentials → OAuth client ID → Web application.**
2. **Authorized redirect URIs:**
   - `http://localhost:8000/integrations/google/callback`
   - Later: `https://<your-render-api-url>/integrations/google/callback`
   - Must match exactly, including `http` vs `https` and trailing slashes.
3. Copy the **Client ID** and **Client secret** into `.env`.

### D5. What to expect in Testing mode
- Users see a "Google hasn't verified this app" warning once. Click **Advanced → Continue**.
- Refresh tokens for restricted scopes expire after 7 days; re-authorize before your viva.
- Public launch with Gmail read or full Drive read needs Google verification plus a paid CASA assessment (see the implementation document, Section 12).

---

## Part E: Deployment accounts (needed for Phase 10 only)

1. Create a **Render** account and connect your GitHub repo.
2. Add a payment method **only when you deploy**. Lean plan is about $24/month all-in (see budget).
3. You will create: FastAPI web service (Starter or Standard), Streamlit service (Starter), PostgreSQL (paid Basic, not the free one which is deleted after 30 days) and a persistent disk for ChromaDB (paid web service required).
4. Paste environment variables into Render's dashboard; never commit them.
5. After deploy: add the Render URL to Google OAuth redirect URIs and Firebase authorized domains.
6. Set spend limits on all API keys before going live.

---

## Part F: Python packages

You do **not** install these by hand one by one. Give the agent this list, let it create `requirements.txt` with **pinned versions**, and install inside `.venv`. Package names are from general knowledge; the agent must verify them and current APIs.

| Purpose | Packages |
|---|---|
| Web/API | `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic`, `pydantic-settings`, `python-dotenv` |
| Database | `sqlalchemy`, `alembic`, `psycopg[binary]` (or `psycopg2-binary`) |
| Memory | `mem0ai` |
| Agent | `langgraph`, `langchain`, `langchain-core` |
| LLM adapters (install only what you use) | `langchain-google-genai` (Gemini), `langchain-ollama` (Ollama), `langchain-openai` (OpenAI-compatible), `langchain-anthropic` |
| Vector DB | `chromadb` |
| Embeddings (optional local) | `sentence-transformers` (large; needs RAM) |
| Documents | `pypdf` (or `pdfplumber`), `python-docx`, `tiktoken` |
| Auth | `firebase-admin` (or `PyJWT` + `cryptography`, `python-jose`) |
| Google APIs | `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `google-api-python-client` |
| Encryption | `cryptography` (Fernet for refresh tokens) |
| Background jobs | `apscheduler` |
| Rate limiting | `slowapi` |
| Frontend | `streamlit`, `requests` (or `httpx`), `plotly` or `altair` for charts |
| Testing/quality | `pytest`, `pytest-asyncio`, `httpx`, `ruff` |

Checks the agent should run after installing:
```bash
pip check
python -c "import fastapi, sqlalchemy, chromadb, mem0, langgraph; print('ok')"
```
(Import names can differ from package names, for example `mem0ai` imports as `mem0`. The agent should confirm.)

**Not installed by pip:** PostgreSQL (Docker), Ollama (its own installer), EchoMind (design ideas only, nothing to install). Cognee and Graphiti are optional Phase 11 packages that also need a graph database; skip them for now.

You do **not** clone the Mem0, LangGraph or ChromaDB repos to build PersonaOS. Clone one only to read its examples, in a separate folder outside your project.

---

## Part G: Secrets and the `.env` file

The agent should create `.env.example` (variable names, no values). You copy it to `.env` and fill in your own values. `.env` must be in `.gitignore`.

### G1. Variables you will likely fill in

| Variable (names may differ) | Where it comes from | Needed by |
|---|---|---|
| `DATABASE_URL` | Local Docker Postgres; you choose the password | Phase 0 |
| `POSTGRES_PASSWORD` | You choose | Phase 0 |
| `LLM_PROVIDER`, `LLM_MODEL` | Your Part B choice | Phase 2 |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Your Part B choice | Phase 2 |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Provider dashboard | Phase 2 |
| `OLLAMA_BASE_URL` | Ollama (if used) | Phase 2 |
| `CHROMA_PATH` | Local folder for persistent Chroma | Phase 2 |
| `FIREBASE_PROJECT_ID` (or Auth0 domain, audience) | Part C | Phase 1 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Part D4 | Phase 7 |
| `GOOGLE_REDIRECT_URI` | Part D4 | Phase 7 |
| `TOKEN_ENCRYPTION_KEY` | Generate (below) | Phase 7 |
| `CRON_SECRET` | Generate (below) | Phase 6 |
| `MAX_UPLOAD_MB`, `DAILY_TOKEN_LIMIT_PER_USER` | You choose | Phase 3 / 9 |
| `FRONTEND_ORIGIN` | Streamlit URL, for CORS | Phase 10 |

### G2. Generating secrets
After `cryptography` is installed:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"                                # CRON_SECRET
```
Back up the encryption key somewhere safe. If you lose it, stored Google tokens cannot be decrypted and users must reconnect.

### G3. Rules
- Never paste real keys into the chat with the agent; put them in `.env` yourself.
- Never commit `.env` or service-account JSON files. If a key leaks, revoke and regenerate it immediately.
- Use separate keys for development and production.

---

## Part H: Readiness checklist (tick before "start Phase 0")

**Computer**
- [ ] Python 3.12+ works, virtual environment created and activated
- [ ] Git works; GitHub repo created and connected
- [ ] Docker runs `hello-world`
- [ ] Coding agent installed and logged in
- [ ] Ollama installed and models pulled (only if using local LLM)

**Project**
- [ ] `docs/` contains the build spec, implementation document and abstract
- [ ] `.gitignore` covers `.env`, `.venv/`, Chroma data and key files
- [ ] First commit pushed
- [ ] Starting instruction pasted to the agent (A4)

**Accounts and keys (can be done just before their phase)**
- [ ] LLM path chosen; key created (or Ollama tested); spend limit set on paid keys
- [ ] Firebase project, Email and Google sign-in enabled, project ID noted, two test users
- [ ] Google Cloud project, three APIs enabled, consent screen in Testing, test users added, OAuth client created
- [ ] Test Gmail account with fake data
- [ ] Render account (only at Phase 10)

**Secrets**
- [ ] `.env` created from `.env.example`
- [ ] Encryption key and cron secret generated and backed up

---

## Part I: Recommended timeline

| When | You do | Agent does |
|---|---|---|
| Day 0 | Parts A, F review, checklist H (computer + project) | Nothing yet |
| Before Phase 1 | Part C (Firebase) | Phase 0 setup, then Phase 1 DB + auth |
| Before Phase 2 | Part B (LLM key or Ollama) | Phase 2 chat + Mem0 |
| Phases 3 to 6 | Answer questions, review each phase report | Documents, goals, agent, memory lifecycle |
| Before Phase 7 | Part D (Google Cloud) | Phase 7 to 8 Calendar, Drive, Gmail |
| Phase 9 | Review UI | Analytics and dashboard |
| Before Phase 10 | Part E (Render) | Hardening, Docker, deploy, docs |
| Before viva | Re-authorize Google, check spend, record a backup video | |

---

## Part J: Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `docker compose up` fails | Docker Desktop not running; on Windows enable WSL 2 |
| App cannot reach Ollama from Docker | Use `http://host.docker.internal:11434` |
| Mem0 calls an unexpected provider | Its LLM/embedder config was not set; configure via `llm.py` |
| Chroma errors after changing embedding model | Vector dimensions differ; delete the collection and re-ingest |
| Google login: `redirect_uri_mismatch` | Redirect URI in Cloud console does not exactly match the app's |
| Google login: "access blocked" | Your account is not listed as a test user |
| Google API stops working after a week | Restricted-scope refresh token expired in Testing mode; reconnect |
| 401 on every request | Wrong Firebase project ID or expired token |
| Free-tier Gemini 429 errors | Rate limit hit; slow down, or switch to a paid key or Ollama |
| Render service slow on first hit | Free tier sleeps; use the paid Starter plan for demos |
