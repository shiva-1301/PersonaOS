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
    reply: str
    memories_used: int


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str
    created_at: datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]
