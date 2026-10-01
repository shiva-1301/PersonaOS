from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.deps import CurrentUser, DbSession
from app.schemas.users import MeOut, MeUpdate
from app.services.privacy_service import CONFIRM_PHRASE, delete_all_user_data

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


class DeleteAllRequest(BaseModel):
    # Must be exactly "DELETE MY DATA": protects against accidental calls.
    confirm: str


@router.delete("/me/data")
def delete_my_data(
    body: DeleteAllRequest, request: Request, user: CurrentUser, db: DbSession
) -> dict:
    """Permanently delete everything PersonaOS stores about you: memories (and their
    history), documents and their chunks, goals, tasks, chats, Google tokens and your
    user record. Returns what was deleted.

    Your Firebase login itself is not deleted here: do that from the app (Firebase
    client SDK `user.delete()`). Signing in again starts a new, empty account."""
    if body.confirm != CONFIRM_PHRASE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f'confirm must be exactly "{CONFIRM_PHRASE}"'
        )
    return {"deleted": delete_all_user_data(db, request.app.state.services, user)}
