"""SQLAlchemy repositories for recruiting."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, Text, cast, func, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.documents.domain.spans import PageRect
from insightlm.recruiting.adapters.models import (
    ApplicationRow,
    AssessmentRow,
    CandidateRow,
    RequisitionRow,
    RubricRow,
    ScorecardRow,
    StageTransitionRow,
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
from insightlm.recruiting.domain.pipeline import RejectionReason, StageTransition
from insightlm.recruiting.domain.rubric import (
    Criterion,
    CriterionKind,
    Requisition,
    RequisitionStatus,
    Rubric,
    RubricStatus,
)
from insightlm.recruiting.domain.scorecard import CriterionRating, Recommendation, Scorecard
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import (
    ApplicationId,
    AssessmentId,
    CandidateId,
    CriterionId,
    DocumentId,
    RequisitionId,
    RubricId,
    ScorecardId,
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
                "document_title": e.document_title,
                "rects": [[r.page, r.x0, r.y0, r.x1, r.y1] for r in e.rects],
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
        evidence=tuple(_evidence_from_json(e) for e in raw.get("evidence", [])),
    )


def _evidence_from_json(raw: dict[str, Any]) -> EvidenceRef:
    fields = {k: v for k, v in raw.items() if k != "rects"}
    rects = tuple(
        PageRect(page=int(page), x0=x0, y0=y0, x1=x1, y1=y1)
        for page, x0, y0, x1, y1 in raw.get("rects", [])
    )
    return EvidenceRef(**fields, rects=rects)


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

    @staticmethod
    def _to_domain(row: CandidateRow) -> Candidate:
        return Candidate(
            id=CandidateId(row.id),
            tenant_id=TenantId(row.tenant_id),
            name=row.name,
            email=row.email,
            document_ids=tuple(DocumentId(UUID(d)) for d in row.document_ids),
            created_at=row.created_at,
        )

    async def get(self, candidate_id: CandidateId) -> Candidate | None:
        row = await self._session.get(CandidateRow, candidate_id)
        return self._to_domain(row) if row else None

    async def with_any_document(self, document_ids: list[DocumentId]) -> list[Candidate]:
        """Candidates whose documents include any of these -- JSONB `?|`, one query."""
        if not document_ids:
            return []
        rows = (
            await self._session.execute(
                select(CandidateRow).where(
                    CandidateRow.document_ids.has_any(
                        cast(array([str(d) for d in document_ids]), ARRAY(Text))
                    )
                )
            )
        ).scalars()
        return [self._to_domain(r) for r in rows]


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
            stage_entered_at=row.stage_entered_at,
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
                stage_entered_at=a.stage_entered_at,
            )
        )

    async def update(self, a: Application) -> None:
        row = await self._session.get(ApplicationRow, a.id)
        if row is None:
            raise NotFoundError("application not found")
        row.stage = a.stage.value
        row.stage_entered_at = a.stage_entered_at

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


class SqlAlchemyStageTransitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, t: StageTransition) -> None:
        self._session.add(
            StageTransitionRow(
                id=uuid4(),
                tenant_id=t.tenant_id,
                application_id=t.application_id,
                from_stage=t.from_stage.value,
                to_stage=t.to_stage.value,
                reason=t.reason.value if t.reason else None,
                note=t.note,
                by=t.by,
                at=t.at,
            )
        )

    async def list_for_application(self, application_id: ApplicationId) -> list[StageTransition]:
        rows = (
            await self._session.execute(
                select(StageTransitionRow)
                .where(StageTransitionRow.application_id == application_id)
                .order_by(StageTransitionRow.at)
            )
        ).scalars()
        return [
            StageTransition(
                tenant_id=TenantId(r.tenant_id),
                application_id=ApplicationId(r.application_id),
                from_stage=Stage(r.from_stage),
                to_stage=Stage(r.to_stage),
                by=UserId(r.by),
                at=r.at,
                reason=RejectionReason(r.reason) if r.reason else None,
                note=r.note,
            )
            for r in rows
        ]


class SqlAlchemyScorecardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_domain(row: ScorecardRow) -> Scorecard:
        return Scorecard(
            id=ScorecardId(row.id),
            tenant_id=TenantId(row.tenant_id),
            application_id=ApplicationId(row.application_id),
            interviewer_id=UserId(row.interviewer_id),
            assigned_by=UserId(row.assigned_by),
            assigned_at=row.assigned_at,
            ratings=tuple(
                CriterionRating(
                    criterion_id=CriterionId(UUID(r["criterion_id"])),
                    rating=int(r["rating"]),
                    note=r.get("note", ""),
                )
                for r in row.ratings
            ),
            recommendation=Recommendation(row.recommendation) if row.recommendation else None,
            summary=row.summary,
            submitted_at=row.submitted_at,
        )

    async def add(self, card: Scorecard, *, requisition_id: RequisitionId) -> None:
        self._session.add(
            ScorecardRow(
                id=card.id,
                tenant_id=card.tenant_id,
                application_id=card.application_id,
                requisition_id=requisition_id,
                interviewer_id=card.interviewer_id,
                assigned_by=card.assigned_by,
                assigned_at=card.assigned_at,
                ratings=[],
                summary="",
            )
        )

    async def get(self, scorecard_id: ScorecardId) -> Scorecard | None:
        row = await self._session.get(ScorecardRow, scorecard_id)
        return self._to_domain(row) if row else None

    async def update(self, card: Scorecard) -> None:
        row = await self._session.get(ScorecardRow, card.id)
        if row is None:
            raise NotFoundError("scorecard not found")
        row.ratings = [
            {"criterion_id": str(r.criterion_id), "rating": r.rating, "note": r.note}
            for r in card.ratings
        ]
        row.recommendation = card.recommendation.value if card.recommendation else None
        row.summary = card.summary
        row.submitted_at = card.submitted_at

    async def list_for_application(self, application_id: ApplicationId) -> list[Scorecard]:
        rows = (
            await self._session.execute(
                select(ScorecardRow)
                .where(ScorecardRow.application_id == application_id)
                .order_by(ScorecardRow.assigned_at)
            )
        ).scalars()
        return [self._to_domain(r) for r in rows]

    async def list_for_interviewer(self, interviewer_id: UserId) -> list[Scorecard]:
        rows = (
            await self._session.execute(
                select(ScorecardRow)
                .where(ScorecardRow.interviewer_id == interviewer_id)
                .order_by(ScorecardRow.assigned_at.desc())
            )
        ).scalars()
        return [self._to_domain(r) for r in rows]

    async def requisitions_for_interviewer(self, interviewer_id: UserId) -> set[RequisitionId]:
        rows = await self._session.execute(
            select(ScorecardRow.requisition_id)
            .where(ScorecardRow.interviewer_id == interviewer_id)
            .distinct()
        )
        return {RequisitionId(r) for r in rows.scalars()}
