"""Internal endpoints for schedulers (cloud cron). Not part of the public API docs."""

import hmac

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.deps import DbSession
from app.jobs.memory_lifecycle import run_memory_lifecycle

router = APIRouter(prefix="/internal", include_in_schema=False)


def _check_secret(request: Request, provided: str | None) -> None:
    secret = request.app.state.settings.CRON_SECRET
    if secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CRON_SECRET is not configured")
    # Constant-time comparison: no timing hints about the secret.
    if not provided or not hmac.compare_digest(
        provided.encode(), secret.get_secret_value().encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron secret")


@router.post("/jobs/memory-lifecycle")
def memory_lifecycle_job(
    request: Request,
    db: DbSession,
    x_cron_secret: str | None = Header(default=None),
) -> dict:
    """Run the nightly memory lifecycle. Header: X-Cron-Secret: <CRON_SECRET>."""
    _check_secret(request, x_cron_secret)
    return run_memory_lifecycle(db, request.app.state.services.memory)
