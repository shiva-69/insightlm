"""The read side of the index.

Split into two ports rather than one because they fail differently and are measured
separately. Phase 3's headline result is what happens when their outputs are fused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from insightlm.shared.domain.ids import ChunkId, DocumentId


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: ChunkId
    score: float
    rank: int


class VectorSearch(Protocol):
    """Dense retrieval. Catches paraphrase -- "built the checkout flow" matching
    "payment integration experience"."""

    async def search(
        self,
        embedding: list[float],
        *,
        tenant_id: object,
        limit: int,
        document_ids: tuple[DocumentId, ...] | None = None,
    ) -> list[SearchHit]:
        """Tenant filtering happens **inside** the SQL, never after.

        Post-filtering an approximate-nearest-neighbour result silently shrinks recall
        (the top-k was chosen before the filter) and is one bug away from a
        cross-tenant leak. Row-level security enforces the same thing underneath.
        """
        ...


class LexicalSearch(Protocol):
    """BM25-style retrieval over Postgres full-text.

    Earns its place on exact terms dense retrieval fumbles -- `Kubernetes`,
    `PCI-DSS`, `CKA`, a specific framework version. In a hiring corpus that is not a
    marginal case, it is the common one. Indexed in Phase 1; fused in Phase 3.
    """

    async def search(
        self,
        query: str,
        *,
        tenant_id: object,
        limit: int,
        document_ids: tuple[DocumentId, ...] | None = None,
    ) -> list[SearchHit]: ...
