"""The `EvidencePort` implementation.

Phase 1 runs dense retrieval only. Everything Phase 3 adds -- lexical fusion via RRF,
cross-encoder reranking, query transformation -- slots in behind this same port, which
is why the ablation is a config sweep rather than a rewrite.

`config_hash` is the point of the `_config_fingerprint` method: it records exactly
which retrieval configuration produced a bundle, so an answer can be re-run under a
different one and the two compared. That is what makes RQ2 and RQ3 measurable rather
than anecdotal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
)
from insightlm.retrieval.domain.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.retrieval.ports.search import LexicalSearch, SearchHit, VectorSearch
from insightlm.shared.domain.ids import TenantId
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


class HybridEvidenceService:
    """Dense retrieval today; the seam for hybrid and reranking tomorrow."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        vector_search: VectorSearch,
        lexical_search: LexicalSearch,
        hydrate: object,
        tenant_id: TenantId,
        candidate_multiplier: int = 4,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._embedder = embedder
        self._vector = vector_search
        self._lexical = lexical_search
        self._hydrate = hydrate
        self._tenant_id = tenant_id
        # Retrieve wider than we return. Phase 1 simply truncates; Phase 3 reranks the
        # wider set, which is where the extra candidates start earning their cost.
        self._candidate_multiplier = candidate_multiplier
        self._rrf_k = rrf_k

    async def find(self, query: EvidenceQuery) -> EvidenceBundle:
        candidate_limit = query.top_k * self._candidate_multiplier

        if query.strategy is RetrievalStrategy.LEXICAL:
            hits = await self._lexical_hits(query, candidate_limit)
        elif query.strategy is RetrievalStrategy.DENSE:
            hits = await self._dense_hits(query, candidate_limit)
        else:
            hits = await self._fused_hits(query, candidate_limit)

        considered = len(hits)
        kept = [hit for hit in hits if hit.score >= query.min_score][: query.top_k]
        spans = await self._hydrate.hydrate(kept, tenant_id=self._tenant_id)  # type: ignore[attr-defined]

        _logger.info(
            "evidence_retrieved",
            strategy=query.strategy.value,
            considered=considered,
            returned=len(spans),
        )

        return EvidenceBundle(
            query=query,
            spans=tuple(spans),
            strategy=query.strategy,
            config_hash=self._config_fingerprint(query),
            candidates_considered=considered,
        )

    async def _dense_hits(self, query: EvidenceQuery, limit: int) -> list[SearchHit]:
        embedding = await self._embedder.embed_query(query.text)
        return await self._vector.search(
            embedding,
            tenant_id=self._tenant_id,
            limit=limit,
            document_ids=query.document_ids,
        )

    async def _lexical_hits(self, query: EvidenceQuery, limit: int) -> list[SearchHit]:
        return await self._lexical.search(
            query.text,
            tenant_id=self._tenant_id,
            limit=limit,
            document_ids=query.document_ids,
        )

    async def _fused_hits(self, query: EvidenceQuery, limit: int) -> list[SearchHit]:
        """Run both arms concurrently and fuse their rankings.

        Concurrently because they are independent I/O against the same database and
        serialising them would double the latency of the arm that is supposed to be
        nearly free. Neither holds the session across the gather -- both use the one
        session they were constructed with, sequentially inside their own call.
        """
        dense, lexical = await asyncio.gather(
            self._dense_hits(query, limit),
            self._lexical_hits(query, limit),
        )

        fused = reciprocal_rank_fusion(
            {
                "dense": [str(hit.chunk_id) for hit in dense],
                "lexical": [str(hit.chunk_id) for hit in lexical],
            },
            k=self._rrf_k,
            limit=limit,
        )

        by_id = {str(hit.chunk_id): hit for hit in (*dense, *lexical)}
        _logger.info(
            "arms_fused",
            dense=len(dense),
            lexical=len(lexical),
            fused=len(fused),
            lexical_only=sum(1 for hit in fused if hit.contributing_arms == ("lexical",)),
        )

        return [
            SearchHit(chunk_id=by_id[hit.key].chunk_id, score=hit.score, rank=rank)
            for rank, hit in enumerate(fused)
            if hit.key in by_id
        ]

    def _config_fingerprint(self, query: EvidenceQuery) -> str:
        """Everything that could change the ranking, hashed.

        Stamped on every answer, so "would this have come out differently under the
        new pipeline?" is a diff rather than a guess.
        """
        payload = {
            "strategy": query.strategy.value,
            "rrf_k": self._rrf_k if query.strategy.value.startswith("hybrid") else None,
            "top_k": query.top_k,
            "min_score": query.min_score,
            "candidate_multiplier": self._candidate_multiplier,
            "embedding_model": self._embedder.model_id,
            "embedding_dimensions": self._embedder.dimensions,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
