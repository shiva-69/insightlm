"""Reviewing a contract against an approved playbook.

1. Refused outright unless the playbook is approved.
2. Evidence per position, through the engine's port, scoped to this contract's
   documents only.
3. One narrow question per position to the judge.
4. Every citation resolved against what was actually retrieved; a finding that cites
   nothing real is discarded as MISSING with zero confidence, which flags the review.
5. The risk summary computed in code.

Retrieval runs sequentially inside one evidence session; only the judging fans out,
under a semaphore, because it does no database I/O.
"""

from __future__ import annotations

import asyncio

from insightlm.contracts.domain.audit_actions import ContractAudit
from insightlm.contracts.domain.ids import ContractReviewId, PlaybookId, new_contract_review_id
from insightlm.contracts.domain.playbook import Position
from insightlm.contracts.domain.review import (
    ClauseEvidence,
    ContractReview,
    Finding,
    PositionFinding,
    summarise,
)
from insightlm.contracts.domain.roles import ContractPermission
from insightlm.contracts.ports.evidence import EvidenceProvider
from insightlm.contracts.ports.judge import ClauseJudge, ClauseJudgment
from insightlm.contracts.ports.repositories import ContractsUnitOfWorkFactory
from insightlm.retrieval.domain.evidence import EvidenceBundle, EvidenceQuery, RetrievalStrategy
from insightlm.shared.domain.errors import ConflictError, NotFoundError, ValidationError
from insightlm.shared.domain.ids import DocumentId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.limits import ModelBudget

_logger = get_logger(__name__)

EVIDENCE_PER_POSITION = 5
MAX_CONCURRENT_JUDGEMENTS = 4


class ReviewContract:
    def __init__(
        self,
        uow_factory: ContractsUnitOfWorkFactory,
        evidence: EvidenceProvider,
        judge: ClauseJudge,
        clock: Clock,
        budget: ModelBudget,
    ) -> None:
        self._uow_factory = uow_factory
        self._evidence = evidence
        self._judge = judge
        self._clock = clock
        self._budget = budget

    async def execute(
        self,
        actor: Actor,
        *,
        title: str,
        playbook_id: PlaybookId,
        document_ids: list[DocumentId],
    ) -> ContractReview:
        require(actor, ContractPermission.CONTRACT_REVIEW)
        if not document_ids:
            raise ValidationError("choose the contract's documents to review")
        if not title.strip():
            raise ValidationError("a review needs a title")

        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            playbook = await uow.playbooks.get(playbook_id)
        if playbook is None:
            raise NotFoundError("playbook not found")
        if not playbook.is_approved:
            raise ConflictError(
                "this playbook is not approved; counsel must approve the positions before "
                "any contract is reviewed against them"
            )

        scope = tuple(dict.fromkeys(document_ids))
        bundles: dict[object, EvidenceBundle] = {}
        async with self._evidence.for_actor(actor) as port:
            for position in playbook.positions:
                bundles[position.id] = await port.find(
                    EvidenceQuery(
                        text=position.query,
                        top_k=EVIDENCE_PER_POSITION,
                        document_ids=scope,
                        strategy=RetrievalStrategy.DENSE,
                    )
                )

        await self._budget.charge(
            actor.tenant_id, sum(1 for b in bundles.values() if not b.is_empty)
        )
        gate = asyncio.Semaphore(MAX_CONCURRENT_JUDGEMENTS)

        async def judged(position: Position) -> ClauseJudgment:
            async with gate:
                return await self._judge.judge(position, bundles[position.id])

        results = await asyncio.gather(*(judged(p) for p in playbook.positions))
        findings = [
            _resolve(position, result, bundles[position.id])
            for position, result in zip(playbook.positions, results, strict=True)
        ]
        summary = summarise(playbook, findings)

        review = ContractReview(
            id=new_contract_review_id(),
            tenant_id=actor.tenant_id,
            title=title.strip(),
            document_ids=scope,
            playbook_id=playbook.id,
            playbook_version=playbook.version,
            findings=tuple(findings),
            summary=summary,
            reviewed_by=actor.user_id,
            created_at=self._clock.now(),
            model_version=results[0].model_version,
            prompt_version=results[0].prompt_version,
            retrieval_config_hash=next(iter(bundles.values())).config_hash,
        )
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            await uow.reviews.add(review)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=ContractAudit.CONTRACT_REVIEWED,
                subject_type="contract_review",
                subject_id=str(review.id),
                payload={
                    "playbook": playbook.name,
                    "playbook_version": playbook.version,
                    "meets": summary.meets,
                    "deviates": summary.deviates,
                    "missing": summary.missing,
                    "flags": [f.value for f in summary.flags],
                },
                model_version=review.model_version,
                prompt_version=review.prompt_version,
            )
            await uow.commit()
        return review


def _resolve(position: Position, result: ClauseJudgment, bundle: EvidenceBundle) -> PositionFinding:
    spans = bundle.by_label()
    evidence = tuple(
        ClauseEvidence(
            label=span.label,
            chunk_id=str(span.chunk_id),
            document_id=str(span.document_id),
            page=span.page,
            char_start=span.span.char_start,
            char_end=span.span.char_end,
            quoted_text=span.text,
            document_title=span.document_title,
            rects=span.span.rects,
        )
        for label in dict.fromkeys(label.strip().upper() for label in result.citation_labels)
        if (span := spans.get(label)) is not None
    )
    if result.finding is not Finding.MISSING and not evidence:
        _logger.warning("clause_finding_downgraded_uncited", clause=position.clause)
        return PositionFinding(
            position_id=position.id,
            finding=Finding.MISSING,
            confidence=0.0,
            rationale=(
                f"The judge returned '{result.finding.value}' but cited no passage that was "
                "retrieved, so the finding was discarded."
            ),
            evidence=(),
        )
    return PositionFinding(
        position_id=position.id,
        finding=result.finding,
        confidence=max(0.0, min(1.0, result.confidence)),
        rationale=result.rationale.strip(),
        evidence=() if result.finding is Finding.MISSING else evidence,
    )


class GetContractReview:
    def __init__(self, uow_factory: ContractsUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, review_id: ContractReviewId) -> ContractReview:
        require(actor, ContractPermission.REVIEW_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            review = await uow.reviews.get(review_id)
        if review is None:
            raise NotFoundError("review not found")
        return review


class ListContractReviews:
    def __init__(self, uow_factory: ContractsUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> list[ContractReview]:
        require(actor, ContractPermission.REVIEW_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            return await uow.reviews.list_for_tenant(actor.tenant_id)
