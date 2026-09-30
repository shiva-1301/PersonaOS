"""FastAPI application factory.

Run with: uvicorn app.main:create_app --factory
"""

import logging

from fastapi import FastAPI

from app.auth.jwt_verify import build_verifier
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.errors import register_error_handlers
from app.logging_config import setup_logging
from app.middleware import RequestIdMiddleware
from app.routers import health, users

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

    app = FastAPI(
        title="PersonaOS API",
        version="0.1.0",
        description="One Identity. One Memory. Infinite Intelligence.",
        # Interactive docs are handy locally but not exposed in production.
        docs_url=None if settings.APP_ENV == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    # The engine connects lazily, so creating it never blocks startup.
    app.state.engine = make_engine(settings.DATABASE_URL)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.verifier = build_verifier(settings)

    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(users.router)

    logger.info(
        "PersonaOS API starting",
        extra={
            "app_env": settings.APP_ENV,
            "llm_provider": settings.LLM_PROVIDER,
            "embedding_provider": settings.EMBEDDING_PROVIDER,
            "auth_provider": settings.AUTH_PROVIDER,
            "google_enabled": settings.google_enabled,
        },
    )
    return app
