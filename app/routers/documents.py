import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, UploadFile, status
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import select

from app.db.models import Document
from app.deps import CurrentUser, DbSession
from app.schemas.documents import DocumentOut, SearchHit, SummaryOut
from app.services.doc_parser import UnsupportedFileType
from app.services.document_service import (
    DocumentNotFound,
    DocumentNotReady,
    create_document,
    delete_document,
    get_owned_document,
    ingest_in_background,
    summarize,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

NOT_FOUND = "Document not found"
READ_CHUNK = 1024 * 1024


def _owned(db, user, document_id) -> Document:
    try:
        return get_owned_document(db, user.id, document_id)
    except DocumentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read at most `limit` bytes; one byte more means the file is too large."""
    data = bytearray()
    while chunk := await file.read(READ_CHUNK):
        data += chunk
        if len(data) > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"File too large (max {limit // (1024 * 1024)} MB)",
            )
    return bytes(data)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
    db: DbSession,
) -> Document:
    """Accepts PDF/DOCX/TXT. Returns immediately with status "processing"; ingestion
    runs in the background and ends in "ready" or "failed" (with a reason)."""
    limit = request.app.state.settings.MAX_UPLOAD_MB * 1024 * 1024
    data = await _read_limited(file, limit)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty")
    try:
        doc = create_document(db, user, file.filename, data)
    except UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    background.add_task(ingest_in_background, request.app, user.id, doc.id, data)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(user: CurrentUser, db: DbSession) -> list[Document]:
    return list(
        db.scalars(
            select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
        )
    )


@router.get("/search", response_model=list[SearchHit])
def search_documents(
    request: Request,
    user: CurrentUser,
    q: str = Query(min_length=1, max_length=1000),
    k: int = Query(default=4, ge=1, le=20),
) -> list[SearchHit]:
    """Semantic search over the current user's document chunks only."""
    try:
        chunks = request.app.state.services.rag.retrieve(user.id, q, k=k)
    except Exception:
        logger.exception("Document search failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document search is temporarily unavailable"
        ) from None
    return [
        SearchHit(
            document_id=c.document_id,
            filename=c.filename,
            chunk_index=c.chunk_index,
            relevance=round(c.relevance, 4),
            text=c.text,
        )
        for c in chunks
    ]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Document:
    return _owned(db, user, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_document(
    document_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession
) -> None:
    doc = _owned(db, user, document_id)
    delete_document(db, request.app.state.services, user.id, doc)


@router.post("/{document_id}/summarize", response_model=SummaryOut)
def summarize_document(
    document_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession
) -> SummaryOut:
    doc = _owned(db, user, document_id)
    try:
        text = summarize(db, request.app.state.services, user.id, doc)
    except DocumentNotReady:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Document is {doc.status}, not ready"
        ) from None
    except ModelRateLimitError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The assistant is busy (rate limited). Please try again in a minute.",
            headers={"Retry-After": "60"},
        ) from None
    except Exception:
        logger.exception("Summary failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The assistant is temporarily unavailable"
        ) from None
    return SummaryOut(document_id=doc.id, summary=text)
