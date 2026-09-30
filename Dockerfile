# Development image for the PersonaOS API. A multi-stage production image follows in Phase 10.
FROM python:3.12-slim

# MEM0_TELEMETRY / ANONYMIZED_TELEMETRY: privacy, no usage telemetry from Mem0 or Chroma.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEM0_TELEMETRY=False \
    ANONYMIZED_TELEMETRY=False

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data/chroma && chown -R app:app /data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app app ./app

USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Apply migrations (idempotent), then start the API.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
