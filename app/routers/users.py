from fastapi import APIRouter

from app.deps import CurrentUser
from app.schemas.users import MeOut

router = APIRouter(tags=["users"])


@router.get("/me", response_model=MeOut)
def read_me(user: CurrentUser) -> MeOut:
    return MeOut.model_validate(user)
