"""Ports for turning a parsed document into a searchable index."""

from __future__ import annotations

from typing import Protocol

from insightlm.documents.domain.entities import ParsedDocument
from insightlm.retrieval.domain.chunks import Chunk
from insightlm.shared.domain.ids import ChunkId, DocumentVersionId, TenantId


class Chunker(Protocol):
    def chunk(
        self,
        parsed: ParsedDocument,
        *,
        tenant_id: TenantId,
        document_version_id: DocumentVersionId,
        document_title: str = "",
    ) -> list[Chunk]:
        """Split parsed text into retrievable units, carrying geometry through.

        Every strategy behind this port is an ablation arm in Phase 3, which is why it
        is a port at all rather than a function.
        """
        ...


class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...

    @property
    def model_id(self) -> str:
        """Recorded on every answer. Changing the embedder invalidates the index, and
        an answer must be attributable to the one that produced it."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batched: one call per chunk is the difference between a 50-résumé upload
        taking seconds and taking minutes."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Separate from :meth:`embed_documents` because asymmetric models prefix
        queries and documents differently, and using the wrong side quietly costs
        recall."""
        ...


class ChunkIndex(Protocol):
    """The write side of the index.

    Chunking and embedding are separate methods because they are separate *resumable*
    pipeline steps. If embedding fails -- a rate limit, a provider outage -- the retry
    must not re-parse a 300-page PDF and re-chunk it. Persisting chunks before their
    vectors is what makes `ingestion_steps` more than decoration.
    """

    async def replace_chunks(self, version_id: DocumentVersionId, chunks: list[Chunk]) -> None:
        """Write chunks with no vectors yet, replacing anything already there.

        Replace rather than append, so re-running ingestion after a pipeline-version
        bump is idempotent instead of duplicating every chunk.
        """
        ...

    async def load_chunks(self, version_id: DocumentVersionId) -> list[Chunk]:
        """Read back what the chunk step produced, so the embed step can resume."""
        ...

    async def attach_embeddings(
        self,
        version_id: DocumentVersionId,
        vectors: dict[ChunkId, list[float]],
        *,
        embedding_model: str,
    ) -> None: ...

    async def delete_for_version(self, version_id: DocumentVersionId) -> None:
        """Used by re-ingestion and, in Phase 6, by erasure."""
        ...
