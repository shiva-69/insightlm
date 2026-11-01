"""SQLAlchemy repositories for contract review."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.contracts.adapters.models import ContractReviewRow, PlaybookRow
from insightlm.contracts.domain.ids import ContractReviewId, PlaybookId, PositionId
from insightlm.contracts.domain.playbook import Playbook, PlaybookStatus, Position, Severity
from insightlm.contracts.domain.review import (
    ClauseEvidence,
    ContractReview,
    Finding,
    PositionFinding,
    ReviewFlag,
    RiskSummary,
)
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import DocumentId, TenantId, UserId


def _position_to_json(p: Position) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "clause": p.clause,
        "standard": p.standard,
        "severity": p.severity.value,
        "evidence_hint": p.evidence_hint,
    }


def _position_from_json(raw: dict[str, Any]) -> Position:
    return Position(
        id=PositionId(UUID(raw["id"])),
        clause=raw["clause"],
        standard=raw["standard"],
        severity=Severity(raw["severity"]),
        evidence_hint=raw.get("evidence_hint", ""),
    )


def _playbook_from_row(row: PlaybookRow) -> Playbook:
    return Playbook(
        id=PlaybookId(row.id),
        tenant_id=TenantId(row.tenant_id),
        name=row.name,
        version=row.version,
        positions=tuple(_position_from_json(p) for p in row.positions),
        proposed_by=UserId(row.proposed_by),
        created_at=row.created_at,
        status=PlaybookStatus(row.status),
        approved_by=UserId(row.approved_by) if row.approved_by else None,
        approved_at=row.approved_at,
    )


class SqlAlchemyPlaybookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, playbook: Playbook) -> None:
        self._session.add(
            PlaybookRow(
                id=playbook.id,
                tenant_id=playbook.tenant_id,
                name=playbook.name,
                version=playbook.version,
                positions=[_position_to_json(p) for p in playbook.positions],
                status=playbook.status.value,
                proposed_by=playbook.proposed_by,
                approved_by=playbook.approved_by,
                approved_at=playbook.approved_at,
                created_at=playbook.created_at,
            )
        )

    async def get(self, playbook_id: PlaybookId) -> Playbook | None:
        row = await self._session.get(PlaybookRow, playbook_id)
        return _playbook_from_row(row) if row else None

    async def update(self, playbook: Playbook) -> None:
        row = await self._session.get(PlaybookRow, playbook.id)
        if row is None:
            raise NotFoundError("playbook not found")
        row.status = playbook.status.value
        row.approved_by = playbook.approved_by
        row.approved_at = playbook.approved_at

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Playbook]:
        rows = (
            await self._session.execute(
                select(PlaybookRow)
                .where(PlaybookRow.tenant_id == tenant_id)
                .order_by(PlaybookRow.name, PlaybookRow.version.desc())
            )
        ).scalars()
        return [_playbook_from_row(r) for r in rows]

    async def approved_named(self, name: str) -> Playbook | None:
        row = (
            await self._session.execute(
                select(PlaybookRow).where(
                    PlaybookRow.name == name, PlaybookRow.status == PlaybookStatus.APPROVED.value
                )
            )
        ).scalar_one_or_none()
        return _playbook_from_row(row) if row else None

    async def latest_version(self, name: str) -> int:
        value = (
            await self._session.execute(
                select(func.max(PlaybookRow.version)).where(PlaybookRow.name == name)
            )
        ).scalar()
        return int(value or 0)


def _finding_to_json(f: PositionFinding) -> dict[str, Any]:
    return {
        "position_id": str(f.position_id),
        "finding": f.finding.value,
        "confidence": f.confidence,
        "rationale": f.rationale,
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
                "rects": [
                    {"page": r.page, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                    for r in e.rects
                ],
            }
            for e in f.evidence
        ],
    }


def _finding_from_json(raw: dict[str, Any]) -> PositionFinding:
    return PositionFinding(
        position_id=PositionId(UUID(raw["position_id"])),
        finding=Finding(raw["finding"]),
        confidence=float(raw["confidence"]),
        rationale=raw["rationale"],
        evidence=tuple(
            ClauseEvidence(
                label=e["label"],
                chunk_id=e["chunk_id"],
                document_id=e["document_id"],
                page=e["page"],
                char_start=e["char_start"],
                char_end=e["char_end"],
                quoted_text=e["quoted_text"],
                document_title=e.get("document_title", ""),
                rects=tuple(PageRect(**r) for r in e.get("rects", [])),
            )
            for e in raw["evidence"]
        ),
    )


def _review_from_row(row: ContractReviewRow) -> ContractReview:
    s = row.summary
    return ContractReview(
        id=ContractReviewId(row.id),
        tenant_id=TenantId(row.tenant_id),
        title=row.title,
        document_ids=tuple(DocumentId(d) for d in row.document_ids),
        playbook_id=PlaybookId(row.playbook_id),
        playbook_version=row.playbook_version,
        findings=tuple(_finding_from_json(f) for f in row.findings),
        summary=RiskSummary(
            meets=s["meets"],
            deviates=s["deviates"],
            missing=s["missing"],
            flags=tuple(ReviewFlag(f) for f in s["flags"]),
        ),
        reviewed_by=UserId(row.reviewed_by),
        created_at=row.created_at,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        retrieval_config_hash=row.retrieval_config_hash,
    )


class SqlAlchemyContractReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, review: ContractReview) -> None:
        self._session.add(
            ContractReviewRow(
                id=review.id,
                tenant_id=review.tenant_id,
                title=review.title,
                document_ids=list(review.document_ids),
                playbook_id=review.playbook_id,
                playbook_version=review.playbook_version,
                findings=[_finding_to_json(f) for f in review.findings],
                summary={
                    "meets": review.summary.meets,
                    "deviates": review.summary.deviates,
                    "missing": review.summary.missing,
                    "flags": [f.value for f in review.summary.flags],
                },
                reviewed_by=review.reviewed_by,
                model_version=review.model_version,
                prompt_version=review.prompt_version,
                retrieval_config_hash=review.retrieval_config_hash,
                created_at=review.created_at,
            )
        )

    async def get(self, review_id: ContractReviewId) -> ContractReview | None:
        row = await self._session.get(ContractReviewRow, review_id)
        return _review_from_row(row) if row else None

    async def list_for_tenant(self, tenant_id: TenantId) -> list[ContractReview]:
        rows = (
            await self._session.execute(
                select(ContractReviewRow)
                .where(ContractReviewRow.tenant_id == tenant_id)
                .order_by(ContractReviewRow.created_at.desc())
            )
        ).scalars()
        return [_review_from_row(r) for r in rows]
