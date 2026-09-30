import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int | None
    source: str
    status: str  # processing | ready | failed
    error: str | None
    chunk_count: int
    summary: str | None
    created_at: datetime
    updated_at: datetime


class SearchHit(BaseModel):
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    relevance: float
    text: str


class SummaryOut(BaseModel):
    document_id: uuid.UUID
    summary: str
