"""A content-addressed embedding cache.

Embedding the same text with the same model always produces the same vector -- it is
a forward pass with no sampling. Paying for it twice is pure waste, and the evaluation
harness was paying for it constantly: every configuration re-ingested the same forty
documents and re-embedded every chunk, and every repeated run did it all again.

Keyed on `sha256(model_id, dimensions, text)`, so a change of model or dimension is a
miss rather than a silently wrong hit. Stored in SQLite: stdlib, persistent across
runs, one file, no service to operate.

**Scope: the evaluation harness only.** It is wired in when `embedding_cache_path` is
set, which the evaluation CLI does and the API does not. A cross-tenant cache in the
request path has a subtle side-channel -- response timing can reveal whether a piece of
text was previously embedded by *someone* -- so it is deliberately kept out of
production rather than argued into it.
"""

from __future__ import annotations

import array
import hashlib
import sqlite3
import threading
from pathlib import Path

from insightlm.retrieval.ports.indexing import Embedder
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


def _key(model_id: str, dimensions: int, kind: str, text: str) -> str:
    # `kind` separates documents from queries: asymmetric models embed them
    # differently, so the same string can legitimately have two vectors.
    material = f"{model_id}\x00{dimensions}\x00{kind}\x00{text}".encode()
    return hashlib.sha256(material).hexdigest()


def _pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


class EmbeddingStore:
    """Thread-safe SQLite store for vectors."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
            )
            self._connection.commit()

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        if not keys:
            return {}
        found: dict[str, list[float]] = {}
        with self._lock:
            # Chunked to stay inside SQLite's bound-parameter limit.
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                placeholders = ",".join("?" * len(batch))
                for key, blob in self._connection.execute(
                    f"SELECT key, vector FROM embeddings WHERE key IN ({placeholders})",  # noqa: S608 - placeholders only
                    batch,
                ):
                    found[key] = _unpack(blob)
        return found

    def put_many(self, entries: dict[str, list[float]]) -> None:
        if not entries:
            return
        with self._lock:
            self._connection.executemany(
                "INSERT OR REPLACE INTO embeddings (key, vector) VALUES (?, ?)",
                [(key, _pack(vector)) for key, vector in entries.items()],
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class CachingEmbedder:
    """Wraps any embedder; only texts never seen before reach the provider."""

    def __init__(self, inner: Embedder, store: EmbeddingStore) -> None:
        self._inner = inner
        self._store = store
        self.hits = 0
        self.misses = 0

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def model_id(self) -> str:
        # The inner model's id, unchanged: vectors from the cache are the inner model's
        # vectors, and the index filters on this -- see PgVectorSearch.
        return self._inner.model_id

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, kind="document")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], kind="query"))[0]

    async def _embed(self, texts: list[str], *, kind: str) -> list[list[float]]:
        keys = [_key(self.model_id, self.dimensions, kind, text) for text in texts]
        cached = self._store.get_many(keys)

        # Deduplicate within the batch too: two identical chunks cost one call.
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in cached and key not in missing:
                missing[key] = text

        self.hits += len(texts) - len(missing)
        self.misses += len(missing)

        if missing:
            fresh = (
                await self._inner.embed_documents(list(missing.values()))
                if kind == "document"
                else [await self._inner.embed_query(text) for text in missing.values()]
            )
            computed = dict(zip(missing, fresh, strict=True))
            self._store.put_many(computed)
            cached.update(computed)

        return [cached[key] for key in keys]
