"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.jwt_verify import AuthError, Claims
from app.db.models import User
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_claims(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Claims:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise _unauthorized("Missing bearer token")
    try:
        return request.app.state.verifier.verify(creds.credentials)
    except AuthError as exc:
        raise _unauthorized(str(exc)) from None


def get_current_user(
    claims: Annotated[Claims, Depends(get_claims)],
    db: DbSession,
) -> User:
    """Find-or-create the user row keyed by the verified `auth_uid`."""
    user = db.scalar(select(User).where(User.auth_uid == claims.uid))
    if user is None:
        user = User(auth_uid=claims.uid, email=claims.email, display_name=claims.name)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent first request created the same user; use that row.
            db.rollback()
            user = db.scalar(select(User).where(User.auth_uid == claims.uid))
            if user is None:
                raise
    elif claims.email and user.email != claims.email:
        user.email = claims.email
        db.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
