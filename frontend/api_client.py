"""Typed client for the PersonaOS API (no Streamlit code here).

Every call sends the caller's Firebase ID token. Failures become one of:
  ApiUnavailable   the API can't be reached (not running, timed out)
  Unauthorized     401: token missing, expired or revoked -> sign in again
  PayloadTooLarge  413: upload over the size limit
  ApiError         any other error, with the API's own user-safe message
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, TypedDict

import httpx

# Chat turns and study plans run a local model: allow minutes, not seconds.
DEFAULT_TIMEOUT = 30.0
SLOW_TIMEOUT = 600.0


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ApiUnavailable(ApiError):
    pass


class Unauthorized(ApiError):
    pass


class PayloadTooLarge(ApiError):
    pass


# --------------------------------------------------------------------------- shapes


class Me(TypedDict):
    id: str
    email: str | None
    display_name: str | None
    timezone: str
    created_at: str


class Progress(TypedDict):
    done: int
    total: int
    ratio: float


class Goal(TypedDict):
    id: str
    title: str
    description: str | None
    target_date: str | None
    status: Literal["active", "completed", "paused"]
    progress: Progress
    created_at: str
    updated_at: str


class Task(TypedDict):
    id: str
    goal_id: str | None
    title: str
    notes: str | None
    due_at: str | None
    est_minutes: int | None
    status: Literal["todo", "doing", "done"]
    completed_at: str | None
    created_at: str
    updated_at: str


class Plan(TypedDict):
    goal_id: str
    tasks: list[Task]
    total_minutes: int
    adjustments: list[str]
    used_preferences: list[str]


class Document(TypedDict):
    id: str
    filename: str
    mime_type: str
    size_bytes: int | None
    source: str
    status: Literal["processing", "ready", "failed"]
    error: str | None
    chunk_count: int
    summary: str | None
    created_at: str
    updated_at: str


class SearchHit(TypedDict):
    document_id: str
    filename: str
    chunk_index: int
    relevance: float
    text: str


class Source(TypedDict):
    document_id: str
    filename: str
    chunk_index: int


class ChatReply(TypedDict):
    session_id: str
    message_id: str
    reply: str
    memories_used: int
    memory_status: str
    sources: list[Source]
    tools_used: list[str]
    pending_confirmations: list[dict]


class ChatSession(TypedDict):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class ChatMessage(TypedDict):
    id: str
    role: str
    content: str
    memory_status: str | None
    created_at: str


class ChatSessionDetail(ChatSession):
    messages: list[ChatMessage]


class Memory(TypedDict):
    id: str
    text: str
    state: Literal["active", "stale", "archived", "superseded"]
    strength: float
    access_count: int
    last_accessed_at: str
    source: str
    superseded_by: str | None
    created_at: str


class AnalyticsSummary(TypedDict):
    timezone: str
    today: str
    due_soon_hours: int
    completions_per_day: list[dict]  # {"date", "completed"}
    completions_per_week: list[dict]  # {"week_start", "completed"}
    completed_last_7_days: int
    streak: dict  # {"current", "longest", "completed_today"}
    tasks: dict  # {"todo", "doing", "done", "overdue", "due_soon"}
    goals: list[dict]  # {"id", "title", "status", "target_date", "done", "total", "ratio"}
    memory_by_state: dict[str, int]


class GoogleStatus(TypedDict):
    configured: bool
    connected: bool
    needs_reconnect: bool
    scopes: list[str]


class CalendarEvent(TypedDict):
    title: str
    start: str | None
    end: str | None
    link: str | None


class Proposal(TypedDict):
    id: str
    title: str
    start_at: str
    end_at: str
    timezone: str
    status: str
    event_link: str | None


@dataclass(frozen=True)
class StreamEvent:
    event: Literal["token", "reset", "tool", "done"]
    data: dict


# --------------------------------------------------------------------------- client


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ApiClient:
    """`token` is the ID token, or a callable returning a fresh one for each request.

    `http` lets tests pass a ready client (e.g. FastAPI's TestClient for the real API)."""

    def __init__(
        self,
        base_url: str,
        token: str | Callable[[], str] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        http: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = http or httpx.Client(base_url=self.base_url, transport=transport)

    # ------------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        token = self._token() if callable(self._token) else self._token
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _unavailable(self, exc: Exception) -> ApiUnavailable:
        if isinstance(exc, httpx.TimeoutException):
            return ApiUnavailable(0, "timeout", "The PersonaOS API took too long to answer.")
        return ApiUnavailable(
            0, "unavailable", f"Can't reach the PersonaOS API at {self.base_url}."
        )

    @staticmethod
    def _error(response: httpx.Response) -> ApiError:
        code, message = "error", response.reason_phrase or f"HTTP {response.status_code}"
        try:
            body = response.json()
            code = body["error"]["code"]
            message = body["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        cls = {401: Unauthorized, 413: PayloadTooLarge}.get(response.status_code, ApiError)
        if response.status_code >= 500 and response.status_code != 503 and code == "error":
            message = "The PersonaOS API had a problem. Please try again."
        return cls(response.status_code, code, message)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
        files: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auth: bool = True,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = self._http.request(
                method,
                path,
                json=json_body,
                params=params,
                files=files,
                headers=self._headers() if auth else {},
                timeout=timeout,
            )
        except httpx.TransportError as exc:
            raise self._unavailable(exc) from None
        if response.status_code >= 400:
            raise self._error(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------ account

    def health(self) -> dict:
        return self._request("GET", "/health", auth=False, timeout=5)

    def me(self) -> Me:
        return self._request("GET", "/me")

    def update_me(self, *, timezone: str | None = None, display_name: str | None = None) -> Me:
        body = {k: v for k, v in {"timezone": timezone, "display_name": display_name}.items() if v}
        return self._request("PATCH", "/me", json_body=body)

    def delete_all_data(self) -> dict:
        return self._request(
            "DELETE", "/me/data", json_body={"confirm": "DELETE MY DATA"}, timeout=120
        )

    # ------------------------------------------------------------------ chat

    def chat(self, message: str, session_id: str | None = None) -> ChatReply:
        body = {"message": message, "session_id": session_id}
        return self._request("POST", "/chat", json_body=body, timeout=SLOW_TIMEOUT)

    def chat_stream(self, message: str, session_id: str | None = None) -> Iterator[StreamEvent]:
        """Server-Sent Events from POST /chat/stream. An `error` event raises ApiError."""
        body = {"message": message, "session_id": session_id}
        try:
            with self._http.stream(
                "POST",
                "/chat/stream",
                json=body,
                headers=self._headers(),
                timeout=SLOW_TIMEOUT,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise self._error(response)
                event = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[len("event: ") :]
                    elif line.startswith("data: "):
                        data = json.loads(line[len("data: ") :])
                        if event == "error":
                            err = data.get("error", {})
                            raise ApiError(
                                502, err.get("code", "error"), err.get("message", "Chat failed")
                            )
                        yield StreamEvent(event, data)
        except httpx.TransportError as exc:
            raise self._unavailable(exc) from None

    def chat_sessions(self) -> list[ChatSession]:
        return self._request("GET", "/chat/sessions")

    def chat_session(self, session_id: str) -> ChatSessionDetail:
        return self._request("GET", f"/chat/sessions/{session_id}")

    # ------------------------------------------------------------------ documents

    def documents(self) -> list[Document]:
        return self._request("GET", "/documents")

    def document(self, document_id: str) -> Document:
        return self._request("GET", f"/documents/{document_id}")

    def upload_document(self, filename: str, content: bytes, mime_type: str) -> Document:
        files = {"file": (filename, content, mime_type or "application/octet-stream")}
        return self._request("POST", "/documents", files=files, timeout=120)

    def delete_document(self, document_id: str) -> None:
        self._request("DELETE", f"/documents/{document_id}")

    def summarize_document(self, document_id: str) -> dict:
        return self._request("POST", f"/documents/{document_id}/summarize", timeout=SLOW_TIMEOUT)

    def search_documents(self, query: str, k: int = 4) -> list[SearchHit]:
        return self._request("GET", "/documents/search", params={"q": query, "k": k}, timeout=60)

    # ------------------------------------------------------------------ goals & tasks

    def goals(self, status: str | None = None) -> list[Goal]:
        return self._request("GET", "/goals", params={"status": status})

    def create_goal(
        self, title: str, *, target_date: date | None = None, description: str | None = None
    ) -> Goal:
        body = {"title": title, "target_date": _iso(target_date), "description": description}
        return self._request("POST", "/goals", json_body=body)

    def update_goal(self, goal_id: str, **changes: Any) -> Goal:
        body = {k: _iso(v) if isinstance(v, date) else v for k, v in changes.items()}
        return self._request("PATCH", f"/goals/{goal_id}", json_body=body)

    def delete_goal(self, goal_id: str) -> None:
        self._request("DELETE", f"/goals/{goal_id}")

    def generate_plan(
        self,
        goal_id: str,
        hours_per_week: float,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        preferences: str | None = None,
    ) -> Plan:
        body = {
            "hours_per_week": hours_per_week,
            "start_date": _iso(start_date),
            "end_date": _iso(end_date),
            "preferences": preferences or None,
        }
        return self._request("POST", f"/goals/{goal_id}/plan", json_body=body, timeout=SLOW_TIMEOUT)

    def tasks(
        self,
        *,
        status: str | None = None,
        due: Literal["today", "this_week", "overdue"] | None = None,
        goal_id: str | None = None,
    ) -> list[Task]:
        return self._request(
            "GET", "/tasks", params={"status": status, "due": due, "goal_id": goal_id}
        )

    def create_task(
        self,
        title: str,
        *,
        due_at: datetime | None = None,
        goal_id: str | None = None,
        est_minutes: int | None = None,
        notes: str | None = None,
    ) -> Task:
        body = {
            "title": title,
            "due_at": _iso(due_at),  # naive = the user's local time (API converts)
            "goal_id": goal_id,
            "est_minutes": est_minutes,
            "notes": notes,
        }
        return self._request("POST", "/tasks", json_body=body)

    def update_task(self, task_id: str, **changes: Any) -> Task:
        body = {k: _iso(v) if isinstance(v, date) else v for k, v in changes.items()}
        return self._request("PATCH", f"/tasks/{task_id}", json_body=body)

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/tasks/{task_id}")

    # ------------------------------------------------------------------ memory & analytics

    def memories(self) -> list[Memory]:
        return self._request("GET", "/memory", timeout=60)

    def memory_health(self) -> dict:
        return self._request("GET", "/memory/health")

    def delete_memory(self, memory_id: str) -> None:
        self._request("DELETE", f"/memory/{memory_id}", timeout=60)

    def analytics(self, days: int = 30, weeks: int = 12) -> AnalyticsSummary:
        return self._request("GET", "/analytics/summary", params={"days": days, "weeks": weeks})

    # ------------------------------------------------------------------ Google Calendar

    def google_status(self) -> GoogleStatus:
        return self._request("GET", "/integrations/google/status")

    def google_connect_url(self) -> str:
        return self._request("GET", "/integrations/google/start")["authorization_url"]

    def google_disconnect(self) -> None:
        self._request("DELETE", "/integrations/google")

    def calendar_events(self, days: int = 7) -> list[CalendarEvent]:
        return self._request("GET", "/integrations/google/events", params={"days": days})

    def proposals(self) -> list[Proposal]:
        return self._request("GET", "/integrations/google/proposals")

    def confirm_proposal(self, proposal_id: str) -> Proposal:
        return self._request(
            "POST", f"/integrations/google/proposals/{proposal_id}/confirm", timeout=60
        )

    def cancel_proposal(self, proposal_id: str) -> Proposal:
        return self._request("POST", f"/integrations/google/proposals/{proposal_id}/cancel")
