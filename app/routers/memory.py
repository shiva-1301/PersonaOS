"""See, inspect and delete what PersonaOS remembers about you."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.deps import CurrentUser, DbSession

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryOut(BaseModel):
    id: str
    text: str
    state: str  # active | stale | archived | superseded
    strength: float
    access_count: int
    last_accessed_at: datetime
    source: str
    superseded_by: str | None
    created_at: datetime


@router.get("", response_model=list[MemoryOut])
def list_memories(request: Request, user: CurrentUser, db: DbSession) -> list[dict]:
    """All of your memories, with their state (superseded and archived ones included)."""
    return request.app.state.services.memory.list_memories(db, user.id)


@router.get("/health")
def memory_health(request: Request, user: CurrentUser, db: DbSession) -> dict:
    """Counts by state and how many memories were created over the last 7 days."""
    return request.app.state.services.memory.health(db, user.id)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, request: Request, user: CurrentUser, db: DbSession) -> None:
    """Forget one memory everywhere (vector store, lifecycle record and history)."""
    if not request.app.state.services.memory.delete_memory(db, user.id, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
