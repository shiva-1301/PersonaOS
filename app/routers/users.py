from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.schemas.users import MeOut, MeUpdate

router = APIRouter(tags=["users"])


@router.get("/me", response_model=MeOut)
def read_me(user: CurrentUser) -> MeOut:
    return MeOut.model_validate(user)


@router.patch("/me", response_model=MeOut)
def update_me(body: MeUpdate, user: CurrentUser, db: DbSession) -> MeOut:
    """Set display name and timezone (used for "today", "this week" and study plans)."""
    for name, value in body.model_dump(exclude_unset=True).items():
        setattr(user, name, value)
    db.commit()
    db.refresh(user)
    return MeOut.model_validate(user)
