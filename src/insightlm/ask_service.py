"""Per-request assembly of the answering pipeline, plus provenance persistence.

Retrieval adapters are session-scoped and tenant-scoped, so they cannot be built once
at startup like the stateless ones. This service builds them for the request, runs the
use case, and writes the provenance row.

It sits beside `bootstrap` rather than inside `retrieval` because it names SQLAlchemy
sessions and concrete adapters -- composition, not domain logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.retrieval.adapters.evidence_service import HybridEvidenceService
from insightlm.retrieval.adapters.models import AnswerRow
from insightlm.retrieval.adapters.search import (
    ChunkHydrator,
    PgVectorSearch,
    PostgresLexicalSearch,
)
from insightlm.retrieval.application.answer import (
    AnswerOutput,
    AnswerQuestion,
    AnswerRequest,
)
from insightlm.retrieval.domain.evidence import EvidenceBundle, EvidenceQuery
from insightlm.retrieval.ports.evidence import EvidencePort
from insightlm.retrieval.ports.generation import GroundedGenerator
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.shared.adapters.db.base import set_actor_context, set_tenant_context
from insightlm.shared.domain.policy import Actor

_INPUT_RATE = 5.00
_OUTPUT_RATE = 25.00
_CACHE_READ_RATE = 0.50


class AskService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        embedder: Embedder,
        generator: GroundedGenerator,
        rrf_k: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._generator = generator
        self._rrf_k = rrf_k

    async def retrieve(self, actor: Actor, request: AnswerRequest) -> EvidenceBundle:
        """Retrieval alone, with no generation call.

        Retrieval metrics -- recall, nDCG, MRR -- are computed from the evidence bundle,
        which exists before the generator runs. Measuring them through `ask` pays for a
        generation call per item whose output is then thrown away; on a four-arm
        ablation that was roughly 120 paid calls buying nothing.
        """
        async with self._session_factory() as session:
            await session.begin()
            await set_tenant_context(session, actor.tenant_id)
            await set_actor_context(session, actor.user_id)
            evidence = self._evidence(session, actor)
            return await evidence.find(
                EvidenceQuery(
                    text=request.question,
                    top_k=request.top_k,
                    document_ids=request.document_ids,
                    strategy=request.strategy,
                )
            )

    @asynccontextmanager
    async def evidence_for(self, actor: Actor) -> AsyncIterator[EvidencePort]:
        """An evidence port scoped to the actor, for other modules to consume.

        This is how `recruiting` gets evidence without importing anything from
        `retrieval` beyond its port: the composition layer opens the session and the
        tenant context, and hands over only the Protocol.
        """
        async with self._session_factory() as session:
            await session.begin()
            await set_tenant_context(session, actor.tenant_id)
            await set_actor_context(session, actor.user_id)
            yield self._evidence(session, actor)

    def _evidence(self, session: AsyncSession, actor: Actor) -> HybridEvidenceService:
        return HybridEvidenceService(
            embedder=self._embedder,
            vector_search=PgVectorSearch(session, embedding_model=self._embedder.model_id),
            lexical_search=PostgresLexicalSearch(session),
            hydrate=ChunkHydrator(session),
            tenant_id=actor.tenant_id,
            rrf_k=self._rrf_k,
        )

    async def ask(self, actor: Actor, request: AnswerRequest) -> AnswerOutput:
        async with self._session_factory() as session:
            await session.begin()
            await set_tenant_context(session, actor.tenant_id)
            await set_actor_context(session, actor.user_id)

            evidence = HybridEvidenceService(
                embedder=self._embedder,
                vector_search=PgVectorSearch(session, embedding_model=self._embedder.model_id),
                lexical_search=PostgresLexicalSearch(session),
                hydrate=ChunkHydrator(session),
                tenant_id=actor.tenant_id,
                rrf_k=self._rrf_k,
            )
            use_case = AnswerQuestion(
                evidence, self._generator, embedding_model=self._embedder.model_id
            )
            output = await use_case.execute(actor, request)

            session.add(_provenance_row(actor, request, output))
            await session.commit()

        return output


def _provenance_row(actor: Actor, request: AnswerRequest, output: AnswerOutput) -> AnswerRow:
    """Everything needed to reproduce this answer, or to diff it against a re-run.

    The retrieval config hash, model version and prompt version together answer
    "would this have come out differently under the new pipeline?" -- which is what
    RQ2 and RQ3 measure, and what an auditor asks for in Phase 6.
    """
    answer = output.answer
    cost = (
        answer.input_tokens * _INPUT_RATE
        + answer.cached_input_tokens * _CACHE_READ_RATE
        + answer.output_tokens * _OUTPUT_RATE
    ) / 1_000_000

    return AnswerRow(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        asked_by=actor.user_id,
        question=request.question,
        outcome=answer.outcome.value,
        claims=[
            {
                "text": claim.text,
                "citations": [
                    {
                        "label": citation.label,
                        "chunk_id": str(citation.chunk_id),
                        "document_id": str(citation.document_id),
                        "page": citation.page,
                        "char_start": citation.char_start,
                        "char_end": citation.char_end,
                    }
                    for citation in claim.citations
                ],
            }
            for claim in answer.claims
        ],
        refusal_reason=answer.refusal_reason,
        strategy=answer.evidence.strategy.value,
        retrieval_config_hash=answer.evidence.config_hash,
        model_version=answer.model_version,
        prompt_version=answer.prompt_version,
        embedding_model=output.embedding_model,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        cached_input_tokens=answer.cached_input_tokens,
        cost_usd=Decimal(f"{cost:.6f}"),
        latency_ms=output.latency_ms,
    )


class EvidenceProviderAdapter:
    """Satisfies `recruiting.ports.EvidenceProvider` over the ask service."""

    def __init__(self, ask: AskService) -> None:
        self._ask = ask

    def for_actor(self, actor: Actor) -> AbstractAsyncContextManager[EvidencePort]:
        return self._ask.evidence_for(actor)
