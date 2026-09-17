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

import hashlib
import json

from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
)
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.retrieval.ports.search import LexicalSearch, VectorSearch
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
    ) -> None:
        self._embedder = embedder
        self._vector = vector_search
        self._lexical = lexical_search
        self._hydrate = hydrate
        self._tenant_id = tenant_id
        # Retrieve wider than we return. Phase 1 simply truncates; Phase 3 reranks the
        # wider set, which is where the extra candidates start earning their cost.
        self._candidate_multiplier = candidate_multiplier

    async def find(self, query: EvidenceQuery) -> EvidenceBundle:
        candidate_limit = query.top_k * self._candidate_multiplier

        if query.strategy is RetrievalStrategy.LEXICAL:
            hits = await self._lexical.search(
                query.text,
                tenant_id=self._tenant_id,
                limit=candidate_limit,
                document_ids=query.document_ids,
            )
        else:
            embedding = await self._embedder.embed_query(query.text)
            hits = await self._vector.search(
                embedding,
                tenant_id=self._tenant_id,
                limit=candidate_limit,
                document_ids=query.document_ids,
            )

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

    def _config_fingerprint(self, query: EvidenceQuery) -> str:
        """Everything that could change the ranking, hashed.

        Stamped on every answer, so "would this have come out differently under the
        new pipeline?" is a diff rather than a guess.
        """
        payload = {
            "strategy": query.strategy.value,
            "top_k": query.top_k,
            "min_score": query.min_score,
            "candidate_multiplier": self._candidate_multiplier,
            "embedding_model": self._embedder.model_id,
            "embedding_dimensions": self._embedder.dimensions,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
