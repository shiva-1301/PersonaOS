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
from app.middleware import RequestIdMiddleware, UploadSizeLimitMiddleware
from app.routers import chat, documents, goals, health, tasks, users
from app.services.container import Services
from app.services.llm import chat_model_name, memory_model_settings

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
    # Models, Chroma and Mem0 are built lazily on first use (see services/container.py).
    app.state.services = Services(settings, app.state.session_factory)

    app.add_middleware(UploadSizeLimitMiddleware, max_bytes=settings.MAX_UPLOAD_MB * 1024 * 1024)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(chat.router)
    app.include_router(documents.router)
    app.include_router(goals.router)
    app.include_router(tasks.router)

    mem = memory_model_settings(settings)
    logger.info(
        "PersonaOS API starting",
        extra={
            "app_env": settings.APP_ENV,
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": chat_model_name(settings),
            "memory_llm": f"{mem.LLM_PROVIDER}:{chat_model_name(mem)}",
            "embedding_provider": settings.EMBEDDING_PROVIDER,
            "embedding_model": settings.EMBEDDING_MODEL,
            "auth_provider": settings.AUTH_PROVIDER,
            "google_enabled": settings.google_enabled,
        },
    )
    return app
