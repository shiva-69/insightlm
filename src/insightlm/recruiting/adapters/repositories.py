"""SQLAlchemy repositories for recruiting."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.recruiting.adapters.models import (
    ApplicationRow,
    AssessmentRow,
    CandidateRow,
    RequisitionRow,
    RubricRow,
)
from insightlm.recruiting.domain.assessment import (
    Application,
    Assessment,
    AssessmentFlag,
    Candidate,
    CriterionJudgment,
    EvidenceRef,
    Stage,
    Verdict,
)
from insightlm.recruiting.domain.rubric import (
    Criterion,
    CriterionKind,
    Requisition,
    RequisitionStatus,
    Rubric,
    RubricStatus,
)
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import (
    ApplicationId,
    AssessmentId,
    CandidateId,
    CriterionId,
    DocumentId,
    RequisitionId,
    RubricId,
    TenantId,
    UserId,
)


def _criterion_to_json(c: Criterion) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "label": c.label,
        "kind": c.kind.value,
        "weight": c.weight,
        "evidence_hint": c.evidence_hint,
        "disqualifying": c.disqualifying,
    }


def _criterion_from_json(raw: dict[str, Any]) -> Criterion:
    return Criterion(
        id=CriterionId(UUID(raw["id"])),
        label=raw["label"],
        kind=CriterionKind(raw["kind"]),
        weight=float(raw["weight"]),
        evidence_hint=raw.get("evidence_hint", ""),
        disqualifying=bool(raw.get("disqualifying", False)),
    )


def _to_rubric(row: RubricRow) -> Rubric:
    return Rubric(
        id=RubricId(row.id),
        tenant_id=TenantId(row.tenant_id),
        requisition_id=RequisitionId(row.requisition_id),
        version=row.version,
        criteria=tuple(_criterion_from_json(c) for c in row.criteria),
        proposed_by=UserId(row.proposed_by),
        created_at=row.created_at,
        status=RubricStatus(row.status),
        approved_by=UserId(row.approved_by) if row.approved_by else None,
        approved_at=row.approved_at,
    )


def _judgment_to_json(j: CriterionJudgment) -> dict[str, Any]:
    return {
        "criterion_id": str(j.criterion_id),
        "verdict": j.verdict.value,
        "confidence": j.confidence,
        "rationale": j.rationale,
        "evidence": [
            {
                "label": e.label,
                "chunk_id": e.chunk_id,
                "document_id": e.document_id,
                "page": e.page,
                "char_start": e.char_start,
                "char_end": e.char_end,
                "quoted_text": e.quoted_text,
            }
            for e in j.evidence
        ],
    }


def _judgment_from_json(raw: dict[str, Any]) -> CriterionJudgment:
    return CriterionJudgment(
        criterion_id=CriterionId(UUID(raw["criterion_id"])),
        verdict=Verdict(raw["verdict"]),
        confidence=float(raw["confidence"]),
        rationale=raw.get("rationale", ""),
        evidence=tuple(EvidenceRef(**e) for e in raw.get("evidence", [])),
    )


class SqlAlchemyRequisitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, r: Requisition) -> None:
        self._session.add(
            RequisitionRow(
                id=r.id,
                tenant_id=r.tenant_id,
                title=r.title,
                description=r.description,
                status=r.status.value,
                created_by=r.created_by,
                created_at=r.created_at,
            )
        )

    async def get(self, requisition_id: RequisitionId) -> Requisition | None:
        row = await self._session.get(RequisitionRow, requisition_id)
        if row is None:
            return None
        return Requisition(
            id=RequisitionId(row.id),
            tenant_id=TenantId(row.tenant_id),
            title=row.title,
            description=row.description,
            created_by=UserId(row.created_by),
            created_at=row.created_at,
            status=RequisitionStatus(row.status),
        )

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Requisition]:
        rows = (
            await self._session.execute(
                select(RequisitionRow)
                .where(RequisitionRow.tenant_id == tenant_id)
                .order_by(RequisitionRow.created_at.desc())
            )
        ).scalars()
        return [
            Requisition(
                id=RequisitionId(r.id),
                tenant_id=TenantId(r.tenant_id),
                title=r.title,
                description=r.description,
                created_by=UserId(r.created_by),
                created_at=r.created_at,
                status=RequisitionStatus(r.status),
            )
            for r in rows
        ]


class SqlAlchemyRubricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, r: Rubric) -> None:
        self._session.add(
            RubricRow(
                id=r.id,
                tenant_id=r.tenant_id,
                requisition_id=r.requisition_id,
                version=r.version,
                criteria=[_criterion_to_json(c) for c in r.criteria],
                status=r.status.value,
                proposed_by=r.proposed_by,
                approved_by=r.approved_by,
                approved_at=r.approved_at,
                created_at=r.created_at,
            )
        )

    async def get(self, rubric_id: RubricId) -> Rubric | None:
        row = await self._session.get(RubricRow, rubric_id)
        return _to_rubric(row) if row else None

    async def update(self, r: Rubric) -> None:
        # Only status and approval fields ever change. Criteria are immutable: a
        # changed rubric is a new version, never an edited one.
        row = await self._session.get(RubricRow, r.id)
        if row is None:
            raise NotFoundError("rubric not found")
        row.status = r.status.value
        row.approved_by = r.approved_by
        row.approved_at = r.approved_at

    async def latest_version(self, requisition_id: RequisitionId) -> int:
        value = await self._session.scalar(
            select(func.max(RubricRow.version)).where(RubricRow.requisition_id == requisition_id)
        )
        return int(value or 0)

    async def approved_for(self, requisition_id: RequisitionId) -> Rubric | None:
        row = (
            await self._session.execute(
                select(RubricRow)
                .where(
                    RubricRow.requisition_id == requisition_id,
                    RubricRow.status == RubricStatus.APPROVED.value,
                )
                .order_by(RubricRow.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _to_rubric(row) if row else None

    async def list_for_requisition(self, requisition_id: RequisitionId) -> list[Rubric]:
        rows = (
            await self._session.execute(
                select(RubricRow)
                .where(RubricRow.requisition_id == requisition_id)
                .order_by(RubricRow.version.desc())
            )
        ).scalars()
        return [_to_rubric(r) for r in rows]


class SqlAlchemyCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, c: Candidate) -> None:
        self._session.add(
            CandidateRow(
                id=c.id,
                tenant_id=c.tenant_id,
                name=c.name,
                email=c.email,
                document_ids=[str(d) for d in c.document_ids],
                created_at=c.created_at,
            )
        )

    async def get(self, candidate_id: CandidateId) -> Candidate | None:
        row = await self._session.get(CandidateRow, candidate_id)
        if row is None:
            return None
        return Candidate(
            id=CandidateId(row.id),
            tenant_id=TenantId(row.tenant_id),
            name=row.name,
            email=row.email,
            document_ids=tuple(DocumentId(UUID(d)) for d in row.document_ids),
            created_at=row.created_at,
        )


class SqlAlchemyApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: ApplicationRow) -> Application:
        return Application(
            id=ApplicationId(row.id),
            tenant_id=TenantId(row.tenant_id),
            candidate_id=CandidateId(row.candidate_id),
            requisition_id=RequisitionId(row.requisition_id),
            stage=Stage(row.stage),
            created_at=row.created_at,
        )

    async def add(self, a: Application) -> None:
        self._session.add(
            ApplicationRow(
                id=a.id,
                tenant_id=a.tenant_id,
                candidate_id=a.candidate_id,
                requisition_id=a.requisition_id,
                stage=a.stage.value,
                created_at=a.created_at,
            )
        )

    async def get(self, application_id: ApplicationId) -> Application | None:
        row = await self._session.get(ApplicationRow, application_id)
        return self._to_domain(row) if row else None

    async def list_for_requisition(self, requisition_id: RequisitionId) -> list[Application]:
        rows = (
            await self._session.execute(
                select(ApplicationRow)
                .where(ApplicationRow.requisition_id == requisition_id)
                .order_by(ApplicationRow.created_at)
            )
        ).scalars()
        return [self._to_domain(r) for r in rows]


class SqlAlchemyAssessmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, a: Assessment) -> None:
        self._session.add(
            AssessmentRow(
                id=a.id,
                tenant_id=a.tenant_id,
                application_id=a.application_id,
                rubric_id=a.rubric_id,
                rubric_version=a.rubric_version,
                judgments=[_judgment_to_json(j) for j in a.judgments],
                score=a.score,
                flags=[f.value for f in a.flags],
                assessed_by=a.assessed_by,
                model_version=a.model_version,
                prompt_version=a.prompt_version,
                retrieval_config_hash=a.retrieval_config_hash,
                created_at=a.created_at,
            )
        )

    async def latest_for(self, application_id: ApplicationId) -> Assessment | None:
        row = (
            await self._session.execute(
                select(AssessmentRow)
                .where(AssessmentRow.application_id == application_id)
                .order_by(AssessmentRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return Assessment(
            id=AssessmentId(row.id),
            tenant_id=TenantId(row.tenant_id),
            application_id=ApplicationId(row.application_id),
            rubric_id=RubricId(row.rubric_id),
            rubric_version=row.rubric_version,
            judgments=tuple(_judgment_from_json(j) for j in row.judgments),
            score=row.score,
            flags=tuple(AssessmentFlag(f) for f in row.flags),
            assessed_by=UserId(row.assessed_by),
            created_at=row.created_at,
            model_version=row.model_version,
            prompt_version=row.prompt_version,
            retrieval_config_hash=row.retrieval_config_hash,
        )
