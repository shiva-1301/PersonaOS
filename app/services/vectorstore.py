"""Single embedded Chroma client (decision D6), shared by Mem0 and document RAG."""

import threading
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

_clients: dict[str, chromadb.ClientAPI] = {}
_lock = threading.Lock()


def get_chroma_client(path: str) -> chromadb.ClientAPI:
    """One PersistentClient per path per process (Chroma expects a single owner)."""
    key = str(Path(path).resolve())
    with _lock:
        if key not in _clients:
            Path(key).mkdir(parents=True, exist_ok=True)
            _clients[key] = chromadb.PersistentClient(
                path=key,
                # Privacy: no usage telemetry leaves the machine.
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
            )
        return _clients[key]


def close_chroma_client(path: str) -> None:
    """Close and forget the client for `path`; the next get reopens it from disk."""
    key = str(Path(path).resolve())
    with _lock:
        client = _clients.pop(key, None)
    if client is not None:
        client.close()
