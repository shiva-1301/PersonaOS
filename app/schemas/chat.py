import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatIn(BaseModel):
    # Upper bound is enforced against settings.MAX_MESSAGE_CHARS in the router.
    message: str = Field(min_length=1)
    session_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class ChatOut(BaseModel):
    session_id: uuid.UUID
    # The user message of this turn; poll GET /chat/sessions/{id} for its memory_status.
    message_id: uuid.UUID
    reply: str
    memories_used: int
    # Memory extraction runs after the reply: "pending" here, later "done" or "failed".
    memory_status: str


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    memory_status: str | None = None
    created_at: datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]
