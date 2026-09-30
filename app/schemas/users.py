import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    display_name: str | None
    timezone: str
    created_at: datetime
