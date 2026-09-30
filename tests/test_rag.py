"""Chunking and the RAG service (offline hashing embedder, temp Chroma)."""

import re
import uuid

import pytest

from app.config import Settings
from app.services.fakes import HashingEmbeddings
from app.services.rag_service import RagService, chunk_text, documents_collection_name
from app.services.vectorstore import close_chroma_client, get_chroma_client

SECTIONS = {
    "regression": "Linear regression predicts a continuous target. The cost function is "
    "mean squared error and gradient descent updates weights using the learning rate. ",
    "trees": "Decision trees split nodes using entropy and information gain. Pruning "
    "reduces overfitting and a random forest averages many trees. ",
    "networks": "Neural networks stack layers of perceptrons with activation functions "
    "such as ReLU and sigmoid, trained by backpropagation. ",
}


def long_text(repeat: int = 30) -> str:
    return "\n\n".join(body * repeat for body in SECTIONS.values())


# --------------------------------------------------------------------------- chunking


def test_short_text_is_one_chunk():
    assert chunk_text("  small note  ", 100, 10) == ["small note"]
    assert chunk_text("   ", 100, 10) == []


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_text("x" * 50, 10, 10)
    with pytest.raises(ValueError):
        Settings(_env_file=None, RAG_CHUNK_CHARS=500, RAG_CHUNK_OVERLAP_CHARS=500)


def test_chunks_respect_size_overlap_and_cover_the_text():
    text = long_text()
    size, overlap = 800, 100
    chunks = chunk_text(text, size, overlap)

    assert all(len(c) <= size for c in chunks)
    # About len / (size - overlap) chunks, never wildly more.
    assert len(text) / size <= len(chunks) <= len(text) / (size - overlap) * 1.5 + 1
    # Neighbours overlap: the start of each chunk appears at the end of the previous one.
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt[:30] in prev
    # Nothing is lost: every sentence appears in some chunk.
    for sentence in re.split(r"(?<=\.) ", text.replace("\n\n", " ")):
        sentence = sentence.strip()
        if sentence:
            assert any(sentence in c for c in chunks), sentence[:40]


def test_chunks_do_not_start_mid_word():
    chunks = chunk_text(long_text(), 700, 120)
    for c in chunks[1:]:
        assert re.match(r"^\w+", c)
        word = re.match(r"^\w+", c).group(0)
        assert word in long_text().split() or word.rstrip(".") in long_text()


def test_default_chunk_is_about_800_tokens():
    s = Settings(_env_file=None)
    assert (s.RAG_CHUNK_CHARS, s.RAG_CHUNK_OVERLAP_CHARS) == (3200, 400)


# --------------------------------------------------------------------------- service


@pytest.fixture
def rag(tmp_path):
    s = Settings(
        _env_file=None,
        EMBEDDING_PROVIDER="fake",
        CHROMA_PATH=str(tmp_path / "chroma"),
        RAG_CHUNK_CHARS=600,
        RAG_CHUNK_OVERLAP_CHARS=80,
    )
    service = RagService(s, HashingEmbeddings(), get_chroma_client(s.CHROMA_PATH))
    yield service
    close_chroma_client(s.CHROMA_PATH)


A, B = uuid.uuid4(), uuid.uuid4()


def test_collection_is_separate_from_mem0(rag):
    assert rag.collection.name == documents_collection_name(rag.settings)
    assert rag.collection.name.startswith("personaos_documents__")
    assert rag.collection.configuration_json["hnsw"]["space"] == "cosine"


def test_retrieval_finds_the_right_chunk(rag):
    doc = uuid.uuid4()
    n = rag.ingest(A, doc, "ml.txt", long_text(repeat=6))
    assert n > 3
    for topic, words in (
        ("trees", "entropy information gain pruning"),
        ("networks", "backpropagation activation ReLU"),
        ("regression", "gradient descent learning rate cost"),
    ):
        top = rag.retrieve(A, words, k=1)[0]
        assert SECTIONS[topic][:40] in top.text, topic
        assert (top.document_id, top.filename) == (doc, "ml.txt")
        assert 0 < top.relevance <= 1


def test_other_user_cannot_retrieve(rag):
    doc = uuid.uuid4()
    rag.ingest(A, doc, "a.txt", long_text(repeat=3))
    assert rag.retrieve(B, "entropy information gain", k=5) == []
    assert rag.retrieve(B, "entropy", k=5, document_id=doc) == []
    assert rag.document_chunks(B, doc) == []
    assert rag.count(B) == 0
    assert rag.count(A, doc) > 0


def test_retrieve_requires_the_user_uuid(rag):
    with pytest.raises(TypeError):
        rag.retrieve(None, "entropy")
    with pytest.raises(TypeError):
        rag.retrieve(str(A), "entropy")  # a string id is not accepted either
    with pytest.raises(TypeError):
        rag.retrieve("entropy")  # user_id is a required positional argument


def test_min_relevance_filters(rag):
    rag.ingest(A, uuid.uuid4(), "a.txt", long_text(repeat=3))
    assert rag.retrieve(A, "entropy pruning", k=3, min_relevance=0.0)
    assert rag.retrieve(A, "entropy pruning", k=3, min_relevance=0.9999) == []


def test_delete_removes_only_that_documents_vectors(rag):
    keep, drop = uuid.uuid4(), uuid.uuid4()
    rag.ingest(A, keep, "keep.txt", long_text(repeat=2))
    rag.ingest(A, drop, "drop.txt", long_text(repeat=2))
    rag.ingest(B, uuid.uuid4(), "b.txt", long_text(repeat=2))
    before_keep = rag.count(A, keep)
    rag.delete_document_chunks(A, drop)
    assert rag.count(A, drop) == 0
    assert rag.count(A, keep) == before_keep
    assert rag.count(B) > 0


def test_user_cannot_delete_another_users_chunks(rag):
    doc = uuid.uuid4()
    rag.ingest(A, doc, "a.txt", long_text(repeat=2))
    rag.delete_document_chunks(B, doc)  # wrong owner: matches nothing
    assert rag.count(A, doc) > 0


def test_reingest_replaces_chunks(rag):
    doc = uuid.uuid4()
    n1 = rag.ingest(A, doc, "a.txt", long_text(repeat=3))
    n2 = rag.ingest(A, doc, "a.txt", long_text(repeat=3))
    assert n1 == n2 == rag.count(A, doc)


def test_document_chunks_are_in_order(rag):
    doc = uuid.uuid4()
    text = long_text(repeat=3)
    rag.ingest(A, doc, "a.txt", text)
    assert rag.document_chunks(A, doc) == chunk_text(text, 600, 80)


def test_relative_margin_keeps_only_close_competitors(rag):
    rag.ingest(A, uuid.uuid4(), "trees.txt", SECTIONS["trees"] * 3)
    rag.ingest(A, uuid.uuid4(), "nets.txt", SECTIONS["networks"] * 3)
    everything = rag.retrieve(A, "entropy information gain pruning", k=5)
    assert len(everything) == 2
    close = rag.retrieve(A, "entropy information gain pruning", k=5, relative_margin=0.05)
    assert [c.filename for c in close] == ["trees.txt"]
    assert everything[0].relevance - everything[1].relevance > 0.05
