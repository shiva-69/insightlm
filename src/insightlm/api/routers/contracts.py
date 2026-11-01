"""Contract review over HTTP: playbooks and reviews."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import RequestModel
from insightlm.contracts.application.playbooks import PositionDraft
from insightlm.contracts.domain.ids import ContractReviewId, PlaybookId
from insightlm.contracts.domain.playbook import PlaybookStatus, Severity
from insightlm.contracts.domain.review import ContractReview, Finding, ReviewFlag
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import DocumentId

router = APIRouter(tags=["contracts"])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PositionIn(BaseModel):
    clause: str = Field(min_length=1, max_length=200)
    standard: str = Field(min_length=1, max_length=2000)
    severity: Severity
    evidence_hint: str = Field(default="", max_length=500)


class ProposePlaybookRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    positions: list[PositionIn] = Field(min_length=1, max_length=40)


class PositionResponse(_Out):
    id: UUID
    clause: str
    standard: str
    severity: Severity
    evidence_hint: str


class PlaybookResponse(_Out):
    id: UUID
    name: str
    version: int
    status: PlaybookStatus
    positions: list[PositionResponse]
    proposed_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime


class ReviewRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    playbook_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=10)


class RectResponse(_Out):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class ClauseEvidenceResponse(_Out):
    label: str
    chunk_id: str
    document_id: str
    document_title: str
    page: int
    char_start: int
    char_end: int
    quoted_text: str
    rects: list[RectResponse]


class FindingResponse(_Out):
    position_id: UUID
    finding: Finding
    confidence: float
    rationale: str
    evidence: list[ClauseEvidenceResponse]


class SummaryResponse(_Out):
    meets: int
    deviates: int
    missing: int
    flags: list[ReviewFlag]
    needs_counsel: bool


class ReviewResponse(_Out):
    id: UUID
    title: str
    document_ids: list[UUID]
    playbook_id: UUID
    playbook_version: int
    findings: list[FindingResponse]
    summary: SummaryResponse
    reviewed_by: UUID
    model_version: str
    prompt_version: str
    retrieval_config_hash: str
    created_at: datetime


@router.get("/playbooks", response_model=list[PlaybookResponse], summary="Playbooks, every version")
async def list_playbooks(actor: ActorDep, container: ContainerDep) -> list[PlaybookResponse]:
    playbooks = await container.contracts.list_playbooks.execute(actor)
    return [PlaybookResponse.model_validate(p) for p in playbooks]


@router.post(
    "/playbooks",
    response_model=PlaybookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a playbook (or a new version of one); counsel must approve it before use",
)
async def propose_playbook(
    body: ProposePlaybookRequest, actor: ActorDep, container: ContainerDep
) -> PlaybookResponse:
    playbook = await container.contracts.propose_playbook.execute(
        actor,
        body.name,
        [PositionDraft(p.clause, p.standard, p.severity, p.evidence_hint) for p in body.positions],
    )
    return PlaybookResponse.model_validate(playbook)


@router.post(
    "/playbooks/{playbook_id}/approve",
    response_model=PlaybookResponse,
    summary="Approve a draft playbook; its author cannot",
)
async def approve_playbook(
    playbook_id: UUID, actor: ActorDep, container: ContainerDep
) -> PlaybookResponse:
    playbook = await container.contracts.approve_playbook.execute(actor, PlaybookId(playbook_id))
    return PlaybookResponse.model_validate(playbook)


@router.post(
    "/contracts/reviews",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Review a contract against an approved playbook; every finding is cited",
)
async def review_contract(
    body: ReviewRequest, actor: ActorDep, container: ContainerDep
) -> ReviewResponse:
    document_ids = [DocumentId(d) for d in body.document_ids]
    async with container.documents.uow(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
        for document_id in document_ids:
            # RLS makes another workspace's document indistinguishable from a missing one.
            if await uow.documents.get(document_id) is None:
                raise ValidationError(f"document {document_id} does not exist")
    review = await container.contracts.review_contract.execute(
        actor, title=body.title, playbook_id=PlaybookId(body.playbook_id), document_ids=document_ids
    )
    return _review(review)


@router.get("/contracts/reviews", response_model=list[ReviewResponse], summary="Contract reviews")
async def list_reviews(actor: ActorDep, container: ContainerDep) -> list[ReviewResponse]:
    return [_review(r) for r in await container.contracts.list_reviews.execute(actor)]


@router.get(
    "/contracts/reviews/{review_id}", response_model=ReviewResponse, summary="One contract review"
)
async def get_review(review_id: UUID, actor: ActorDep, container: ContainerDep) -> ReviewResponse:
    review = await container.contracts.get_review.execute(actor, ContractReviewId(review_id))
    return _review(review)


def _review(review: ContractReview) -> ReviewResponse:
    return ReviewResponse.model_validate(review)
