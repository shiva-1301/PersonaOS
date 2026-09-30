"""Document chunks in their own Chroma collection, always scoped by user_id.

Chunking is character-based (~4 chars per token): RAG_CHUNK_CHARS=3200 is about 800
tokens, RAG_CHUNK_OVERLAP_CHARS=400 about 100. No tokenizer dependency (tiktoken would
download vocab files at runtime and is OpenAI-specific anyway).
"""

import logging
import uuid
from dataclasses import dataclass

from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.services.llm import embedding_space_id

logger = logging.getLogger(__name__)

EMBED_BATCH = 16
# Preferred split points, strongest first; searched only in the last quarter of a window.
_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", " ")


def documents_collection_name(settings: Settings) -> str:
    return f"personaos_documents__{embedding_space_id(settings)}"


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split into ~`size`-char chunks overlapping by ~`overlap` chars, on natural breaks."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            floor = int(size * 0.75)
            for sep in _SEPARATORS:
                i = window.rfind(sep, floor)
                if i != -1:
                    end = start + i + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        space = text.find(" ", next_start, end)  # don't start mid-word
        start = space + 1 if space != -1 else next_start
    return chunks


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    text: str
    relevance: float  # cosine similarity, 1.0 = identical


def _require_user(user_id: uuid.UUID) -> str:
    if not isinstance(user_id, uuid.UUID):
        raise TypeError("user_id must be the authenticated user's UUID")
    return str(user_id)


def _where(user_id: uuid.UUID, document_id: uuid.UUID | None = None) -> dict:
    uid = _require_user(user_id)
    if document_id is None:
        return {"user_id": uid}
    return {"$and": [{"user_id": uid}, {"document_id": str(document_id)}]}


class RagService:
    def __init__(self, settings: Settings, embedder: Embeddings, chroma):
        self.settings = settings
        self.embedder = embedder
        self.collection = chroma.get_or_create_collection(
            name=documents_collection_name(settings),
            configuration={"hnsw": {"space": "cosine"}},
            embedding_function=None,  # we always pass our own embeddings
        )

    # ------------------------------------------------------------------ write

    def ingest(self, user_id: uuid.UUID, document_id: uuid.UUID, filename: str, text: str) -> int:
        """Chunk, embed and store a document. Idempotent: replaces earlier chunks."""
        uid = _require_user(user_id)
        chunks = chunk_text(
            text, self.settings.RAG_CHUNK_CHARS, self.settings.RAG_CHUNK_OVERLAP_CHARS
        )
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            vectors.extend(self.embedder.embed_documents(chunks[i : i + EMBED_BATCH]))
        self.delete_document_chunks(user_id, document_id)
        if chunks:
            self.collection.add(
                ids=[f"{document_id}:{i}" for i in range(len(chunks))],
                embeddings=vectors,
                documents=chunks,
                metadatas=[
                    {
                        "user_id": uid,
                        "document_id": str(document_id),
                        "filename": filename,
                        "chunk_index": i,
                    }
                    for i in range(len(chunks))
                ],
            )
        return len(chunks)

    def delete_document_chunks(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        self.collection.delete(where=_where(user_id, document_id))

    # ------------------------------------------------------------------ read

    def retrieve(
        self,
        user_id: uuid.UUID,
        query: str,
        /,
        k: int = 4,
        *,
        document_id: uuid.UUID | None = None,
        min_relevance: float = 0.0,
        relative_margin: float | None = None,
    ) -> list[RetrievedChunk]:
        """Top-k chunks of THIS user's documents. user_id is required and positional.

        `min_relevance` drops weak matches; `relative_margin` also drops chunks scoring
        more than that below the best one (keeps loosely related documents out).
        """
        where = _where(user_id, document_id)
        query = (query or "").strip()
        if not query:
            return []
        result = self.collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        chunks = []
        for text, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            if meta.get("user_id") != str(user_id):
                continue  # defence in depth; the where-filter already guarantees this
            relevance = 1.0 - float(distance)
            if relevance < min_relevance:
                continue
            chunks.append(
                RetrievedChunk(
                    document_id=uuid.UUID(meta["document_id"]),
                    filename=meta.get("filename", ""),
                    chunk_index=int(meta.get("chunk_index", 0)),
                    text=text,
                    relevance=relevance,
                )
            )
        if relative_margin is not None and chunks:
            best = max(c.relevance for c in chunks)
            chunks = [c for c in chunks if c.relevance >= best - relative_margin]
        return chunks

    def document_chunks(self, user_id: uuid.UUID, document_id: uuid.UUID) -> list[str]:
        """All chunks of one of the user's documents, in order."""
        got = self.collection.get(
            where=_where(user_id, document_id), include=["documents", "metadatas"]
        )
        pairs = sorted(
            zip(got["metadatas"], got["documents"], strict=True),
            key=lambda p: p[0]["chunk_index"],
        )
        return [text for _, text in pairs]

    def count(self, user_id: uuid.UUID, document_id: uuid.UUID | None = None) -> int:
        return len(self.collection.get(where=_where(user_id, document_id), include=[])["ids"])
