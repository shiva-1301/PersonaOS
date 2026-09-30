import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.time_utils import is_valid_timezone


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    display_name: str | None
    timezone: str
    created_at: datetime


class MeUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    # IANA name, e.g. "Asia/Kolkata" or "Europe/London".
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("timezone cannot be null")
        if not is_valid_timezone(v):
            raise ValueError(f"unknown timezone {v!r} (use an IANA name like 'Asia/Kolkata')")
        return v
