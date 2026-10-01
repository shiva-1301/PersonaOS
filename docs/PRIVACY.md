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
| Google Calendar connection (Phase 8): an encrypted refresh token | PostgreSQL `google_tokens` | Creating calendar events |

Models run locally by default (Ollama), so prompts don't leave your machine. If you configure a cloud model such as Gemini, your messages and the relevant memories and excerpts are sent to that provider. On free tiers the provider may use them, so use only non-sensitive data there.

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
| **Delete everything** | `DELETE /me/data` with body `{"confirm": "DELETE MY DATA"}` |

`DELETE /me/data` removes, for your account only:
- the memories in ChromaDB;
- Mem0's history rows and buffered messages in SQLite;
- the document chunks in ChromaDB;
- your user record, which also removes your chats, goals, tasks, documents, memory records and Google connection.

The response lists how much was deleted from each store. This is verified by tests that check every store is empty for the deleted user and unchanged for everyone else.

Your Firebase login isn't deleted by this call. Delete it from the app (Phase 9 adds the button). If you sign in again before that, you start with a new, empty account.

## Retention

Data is kept until you delete it. Archived memories stay until you delete them or your account. Backups are covered in `docs/DEPLOYMENT.md` (Phase 10).
