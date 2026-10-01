# PersonaOS: what is stored, and how to see or delete it

PersonaOS keeps every piece of data tied to your account. No other user can read it. The API checks your login on every request, and every lookup is filtered by your user ID. Document text is always treated as data, never as instructions.

## What is stored where

| Data | Where | Why |
|---|---|---|
| Your account (ID, email, display name, timezone) | PostgreSQL `users` | To know who you are; the timezone decides what "today" and "this week" mean |
| Chats (your messages and the assistant's replies) | PostgreSQL `chat_sessions`, `chat_messages` | Conversation history |
| Goals and tasks, including generated study plans | PostgreSQL `goals`, `tasks` | Planning and progress |
| Uploaded documents: filename, type, size, status, summary | PostgreSQL `documents` | Document list and summaries |
| Document text, split into chunks with their embeddings | ChromaDB collection `personaos_documents__*` | Searching your notes. The original file isn't kept. |
| Memories: short facts about you extracted from chat, or saved when you say "remember that…" | ChromaDB collection `personaos_mem0__*` (text and embedding) | Personalisation |
| Memory lifecycle: state, strength, access count, last access, what replaced it | PostgreSQL `memory_meta` | Forgetting curve and supersession |
| Mem0's own bookkeeping: memory change history and a buffer of your recent messages | SQLite `mem0_history.db` (next to ChromaDB) | Used by Mem0 when extracting memories |
| Google Calendar connection: the refresh token, **encrypted** with `TOKEN_ENCRYPTION_KEY`, plus the granted scope | PostgreSQL `google_tokens` | Reading your events and creating events you confirmed |
| Calendar event proposals: title and times the assistant suggested, the chat they were made in, and whether you confirmed or cancelled | PostgreSQL `calendar_proposals` | Events are created only after you confirm |
| Sign-in-with-Google handshakes in progress (a random value, expires after 10 minutes, single use) | PostgreSQL `oauth_states` | Protecting the Google connection flow |

Google access is limited to your calendar events (`calendar.events`). PersonaOS can read your upcoming events, and it creates an event only after you confirm a proposal. It never edits or deletes events. Your Google tokens are never logged or shown. While the app is in Google's Testing mode, the connection expires after 7 days and the app asks you to reconnect.

Models run locally by default (Ollama), so prompts don't leave your machine. If you configure a cloud model such as Gemini, your messages and the relevant memories and excerpts are sent to that provider. On free tiers the provider may use them, so use only non-sensitive data there.

## The web app

- The Streamlit app keeps your Firebase ID token and refresh token only in your browser tab's session on the Streamlit server. They aren't written to disk or logged, and signing out clears them.
- The UI container gets only the API address and the Firebase web API key. It has no database password, Google secret or encryption key.
- Dashboard numbers (`GET /analytics/summary`) are computed when you open the page. Nothing extra is stored.

## How memories change over time

- Memories you don't use fade: **active → stale → archived**. Archived memories are kept but no longer used in answers.
- Using a memory strengthens it, and frequently used memories fade more slowly.
- When you state a new fact that contradicts an older one (for example "Actually, I now study in the mornings"), the old memory is marked **superseded** and no longer used.
- A nightly job (`POST /internal/jobs/memory-lifecycle`, protected by `CRON_SECRET`) applies the forgetting curve.

## See and delete your data

| What | How |
|---|---|
| See all memories, with their state | `GET /memory` |
| Memory overview (counts by state, last 7 days) | `GET /memory/health` |
| Forget one memory everywhere (vector, lifecycle record, Mem0 history) | `DELETE /memory/{id}` |
| Delete a document (record **and** its chunks) | `DELETE /documents/{id}` |
| Disconnect Google Calendar (access revoked at Google, token deleted) | `DELETE /integrations/google` |
| **Delete everything** | `DELETE /me/data` with body `{"confirm": "DELETE MY DATA"}` |

`DELETE /me/data` removes, for your account only:
- the memories in ChromaDB;
- Mem0's history rows and buffered messages in SQLite;
- the document chunks in ChromaDB;
- your Google connection: access is **revoked at Google** first, then the token is deleted;
- your user record, which also removes your chats, goals, tasks, documents, memory records, calendar proposals and sign-in handshakes.

Events already in your Google Calendar stay there: they're yours, in your Google account.

The response lists how much was deleted from each store. This is verified by tests that check every store is empty for the deleted user and unchanged for everyone else.

`DELETE /me/data` doesn't delete your Firebase login. In the app, **Memory → Delete everything → "Also delete my login"** deletes both. Firebase only allows that within a few minutes of signing in; the app checks this before deleting anything. If you keep the login and sign in again, you start with a new, empty account.

## Retention

Data is kept until you delete it. Archived memories stay until you delete them or your account. Backups are covered in `docs/DEPLOYMENT.md` (Phase 10).
