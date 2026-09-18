"""Requisitions, rubrics, candidates and evidence-backed assessments.

The router is thin by rule, with one exception it earns: checking that a candidate's
documents exist. `recruiting` may not import `documents`, and a candidate pointing at
missing documents would score "absent" on every criterion -- a wrong answer that looks
exactly like a right one. So the delivery layer, which may see both, checks first.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.recruiting.application.requisitions import CriterionDraft
from insightlm.recruiting.domain.assessment import (
    AssessmentFlag,
    Stage,
    Verdict,
)
from insightlm.recruiting.domain.pipeline import is_overdue
from insightlm.recruiting.domain.rubric import (
    CriterionKind,
    RequisitionStatus,
    RubricStatus,
)
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import (
    ApplicationId,
    DocumentId,
    RequisitionId,
    RubricId,
)

router = APIRouter(tags=["recruiting"])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- requests ------------------------------------------------------------------------


class CreateRequisitionRequest(_In):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)
    blind_screening: bool = Field(
        default=True, description="Judges never see name, contact, institution or demographics."
    )


class CriterionRequest(_In):
    label: str = Field(min_length=1, max_length=200)
    kind: CriterionKind
    weight: float = Field(gt=0, le=1)
    evidence_hint: str = Field(default="", max_length=500)
    disqualifying: bool = False


class ProposeRubricRequest(_In):
    criteria: list[CriterionRequest] = Field(min_length=1, max_length=25)


class AddCandidateRequest(_In):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    document_ids: list[UUID] = Field(min_length=1, max_length=10)


# -- responses -----------------------------------------------------------------------


class RequisitionResponse(_Out):
    id: UUID
    title: str
    description: str
    status: RequisitionStatus
    blind_screening: bool
    created_by: UUID
    created_at: datetime


class CriterionResponse(_Out):
    id: UUID
    label: str
    kind: CriterionKind
    weight: float
    evidence_hint: str
    disqualifying: bool


class RubricResponse(_Out):
    id: UUID
    requisition_id: UUID
    version: int
    status: RubricStatus
    criteria: list[CriterionResponse]
    proposed_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime


class RequisitionDetailResponse(BaseModel):
    requisition: RequisitionResponse
    rubrics: list[RubricResponse]


class CandidateResponse(_Out):
    id: UUID
    name: str
    email: str | None
    document_ids: list[UUID]
    created_at: datetime


class ApplicationResponse(_Out):
    id: UUID
    candidate_id: UUID
    requisition_id: UUID
    stage: Stage
    stage_entered_at: datetime
    created_at: datetime


class RectResponse(_Out):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class EvidenceRefResponse(_Out):
    label: str
    chunk_id: str
    document_id: str
    page: int
    char_start: int
    char_end: int
    quoted_text: str
    document_title: str
    rects: list[RectResponse]


class JudgmentResponse(_Out):
    criterion_id: UUID
    verdict: Verdict
    confidence: float
    rationale: str
    evidence: list[EvidenceRefResponse]


class AssessmentResponse(_Out):
    id: UUID
    application_id: UUID
    rubric_id: UUID
    rubric_version: int
    score: float
    flags: list[AssessmentFlag]
    judgments: list[JudgmentResponse]
    needs_human_review: bool
    model_version: str
    prompt_version: str
    retrieval_config_hash: str
    redaction_profile: str
    assessed_by: UUID
    created_at: datetime


class AddCandidateResponse(BaseModel):
    candidate: CandidateResponse
    application: ApplicationResponse


class ShortlistEntryResponse(BaseModel):
    application: ApplicationResponse
    candidate: CandidateResponse
    assessment: AssessmentResponse | None
    overdue: bool = Field(description="In the current stage longer than its SLA allows.")


# -- routes --------------------------------------------------------------------------


@router.post(
    "/requisitions",
    response_model=RequisitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a requisition",
)
async def create_requisition(
    body: CreateRequisitionRequest, actor: ActorDep, container: ContainerDep
) -> RequisitionResponse:
    requisition = await container.create_requisition.execute(
        actor, title=body.title, description=body.description, blind_screening=body.blind_screening
    )
    return RequisitionResponse.model_validate(requisition)


@router.get("/requisitions", response_model=list[RequisitionResponse], summary="List requisitions")
async def list_requisitions(actor: ActorDep, container: ContainerDep) -> list[RequisitionResponse]:
    return [
        RequisitionResponse.model_validate(r)
        for r in await container.list_requisitions.execute(actor)
    ]


@router.get(
    "/requisitions/{requisition_id}",
    response_model=RequisitionDetailResponse,
    summary="A requisition and every version of its rubric",
)
async def get_requisition(
    requisition_id: UUID, actor: ActorDep, container: ContainerDep
) -> RequisitionDetailResponse:
    detail = await container.get_requisition.execute(actor, RequisitionId(requisition_id))
    return RequisitionDetailResponse(
        requisition=RequisitionResponse.model_validate(detail.requisition),
        rubrics=[RubricResponse.model_validate(r) for r in detail.rubrics],
    )


@router.post(
    "/requisitions/{requisition_id}/rubrics",
    response_model=RubricResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a new rubric version (a draft until someone approves it)",
)
async def propose_rubric(
    requisition_id: UUID, body: ProposeRubricRequest, actor: ActorDep, container: ContainerDep
) -> RubricResponse:
    rubric = await container.propose_rubric.execute(
        actor,
        RequisitionId(requisition_id),
        [
            CriterionDraft(
                label=c.label,
                kind=c.kind,
                weight=c.weight,
                evidence_hint=c.evidence_hint,
                disqualifying=c.disqualifying,
            )
            for c in body.criteria
        ],
    )
    return RubricResponse.model_validate(rubric)


@router.post(
    "/rubrics/{rubric_id}/approve",
    response_model=RubricResponse,
    summary="Approve a draft rubric; the previous approved version is superseded",
)
async def approve_rubric(
    rubric_id: UUID, actor: ActorDep, container: ContainerDep
) -> RubricResponse:
    rubric = await container.approve_rubric.execute(actor, RubricId(rubric_id))
    return RubricResponse.model_validate(rubric)


@router.post(
    "/requisitions/{requisition_id}/candidates",
    response_model=AddCandidateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a candidate, backed by documents already uploaded",
)
async def add_candidate(
    requisition_id: UUID, body: AddCandidateRequest, actor: ActorDep, container: ContainerDep
) -> AddCandidateResponse:
    document_ids = [DocumentId(d) for d in body.document_ids]
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        for document_id in document_ids:
            # RLS makes another tenant's document indistinguishable from a missing one,
            # which is the point: the error cannot be used to probe for ids.
            if await uow.documents.get(document_id) is None:
                raise ValidationError(f"document {document_id} does not exist")

    candidate, application = await container.add_candidate.execute(
        actor,
        RequisitionId(requisition_id),
        name=body.name,
        email=str(body.email) if body.email else None,
        document_ids=document_ids,
    )
    return AddCandidateResponse(
        candidate=CandidateResponse.model_validate(candidate),
        application=ApplicationResponse.model_validate(application),
    )


@router.post(
    "/applications/{application_id}/assessments",
    response_model=AssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assess an application against the approved rubric; every verdict is cited",
)
async def run_assessment(
    application_id: UUID, actor: ActorDep, container: ContainerDep
) -> AssessmentResponse:
    assessment = await container.run_assessment.execute(actor, ApplicationId(application_id))
    return AssessmentResponse.model_validate(assessment)


@router.get(
    "/requisitions/{requisition_id}/shortlist",
    response_model=list[ShortlistEntryResponse],
    summary="Applications ranked by their latest assessment",
)
async def shortlist(
    requisition_id: UUID, actor: ActorDep, container: ContainerDep
) -> list[ShortlistEntryResponse]:
    entries = await container.shortlist.execute(actor, RequisitionId(requisition_id))
    now = container.clock.now()
    return [
        ShortlistEntryResponse(
            application=ApplicationResponse.model_validate(e.application),
            candidate=CandidateResponse.model_validate(e.candidate),
            assessment=(AssessmentResponse.model_validate(e.assessment) if e.assessment else None),
            overdue=is_overdue(e.application, now),
        )
        for e in entries
    ]
