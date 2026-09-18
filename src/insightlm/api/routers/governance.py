"""Subject access and retention: what we hold about a person, and when it goes."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.routers.pipeline import ScorecardResponse, TransitionResponse
from insightlm.api.routers.recruiting import (
    ApplicationResponse,
    AssessmentResponse,
    CandidateResponse,
    ErasureResponse,
    RequisitionResponse,
    ResidueResponse,
    _Out,
)
from insightlm.shared.domain.ids import CandidateId, DocumentId

router = APIRouter(tags=["governance"])


class ApplicationRecordResponse(_Out):
    application: ApplicationResponse
    requisition: RequisitionResponse | None
    transitions: list[TransitionResponse]
    assessment: AssessmentResponse | None
    scorecards: list[ScorecardResponse]


class DocumentHeld(BaseModel):
    id: UUID
    title: str
    media_type: str
    uploaded_at: datetime
    findings: int


class CandidateRecordResponse(BaseModel):
    candidate: CandidateResponse
    documents: list[DocumentHeld]
    applications: list[ApplicationRecordResponse]
    self_identification: dict[str, str]


@router.get(
    "/candidates/{candidate_id}/record",
    response_model=CandidateRecordResponse,
    summary="Everything held about a candidate (the answer to a subject-access request)",
)
async def candidate_record(
    candidate_id: UUID, actor: ActorDep, container: ContainerDep
) -> CandidateRecordResponse:
    record = await container.candidate_record.execute(actor, CandidateId(candidate_id))
    documents: list[DocumentHeld] = []
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        for document_id in record.candidate.document_ids:
            document = await uow.documents.get(DocumentId(document_id))
            version = await uow.versions.latest_for_document(DocumentId(document_id))
            extracted = await uow.versions.load_text(version.id) if version else None
            if document is not None:
                documents.append(
                    DocumentHeld(
                        id=document.id,
                        title=document.title,
                        media_type=document.media_type.value,
                        uploaded_at=document.created_at,
                        findings=len(extracted.findings) if extracted else 0,
                    )
                )
    return CandidateRecordResponse(
        candidate=CandidateResponse.model_validate(record.candidate),
        documents=documents,
        applications=[ApplicationRecordResponse.model_validate(a) for a in record.applications],
        self_identification={i.dimension.value: i.value for i in record.self_identification},
    )


class RetentionCandidate(BaseModel):
    candidate_id: str
    last_activity: datetime


class RetentionRunResponse(BaseModel):
    retention_days: int
    dry_run: bool
    expired: list[RetentionCandidate]
    erased: list[ErasureResponse]


@router.post(
    "/governance/retention/run",
    response_model=RetentionRunResponse,
    summary="Erase rejected candidates past retention (dry run by default)",
)
async def run_retention(
    actor: ActorDep, container: ContainerDep, dry_run: bool = True
) -> RetentionRunResponse:
    expired = await container.find_expired_candidates.execute(actor)
    erased: list[Any] = []
    if not dry_run:
        for item in expired:
            report = await container.erase_candidate.execute(actor, item.candidate_id)
            erased.append(
                ErasureResponse(
                    candidate_id=report.candidate_id,
                    documents_erased=report.documents_erased,
                    documents_kept_shared=report.documents_kept_shared,
                    files_deleted=report.files_deleted,
                    answers_deleted=report.answers_deleted,
                    events_deleted=report.events_deleted,
                    verified=report.verified,
                    residue=[
                        ResidueResponse(table=r.table, column=r.column, fingerprint=r.fingerprint)
                        for r in report.residue
                    ],
                )
            )
    return RetentionRunResponse(
        retention_days=container.settings.candidate_retention_days,
        dry_run=dry_run,
        expired=[
            RetentionCandidate(candidate_id=str(e.candidate_id), last_activity=e.last_activity)
            for e in expired
        ],
        erased=erased,
    )
