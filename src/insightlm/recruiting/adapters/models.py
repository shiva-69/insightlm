"""Persistence for recruiting.

Criteria live on the rubric row and judgments on the assessment row, as JSONB
snapshots rather than child tables. Both are immutable once written -- an approved
rubric is never edited, it is superseded; an assessment is never revised, it is re-run
-- and neither is queried relationally. A snapshot states that directly; child tables
would imply an editability the domain forbids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insightlm.shared.adapters.db.base import Base


class RequisitionRow(Base):
    __tablename__ = "requisitions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    blind_screening: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_requisitions_tenant_created", "tenant_id", "created_at"),)


class RubricRow(Base):
    __tablename__ = "rubrics"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    requisition_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    criteria: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    requisition: Mapped[RequisitionRow] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("requisition_id", "version", name="uq_rubrics_requisition_version"),
    )


class CandidateRow(Base):
    __tablename__ = "candidates"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Document ids, not a foreign key into another module's table: the documents
    # module owns documents, and recruiting refers to them by id only.
    document_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApplicationRow(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    requisition_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    stage_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[CandidateRow] = relationship(lazy="raise")
    requisition: Mapped[RequisitionRow] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "requisition_id", name="uq_applications_candidate_requisition"
        ),
        Index("ix_applications_requisition", "tenant_id", "requisition_id"),
    )


class AssessmentRow(Base):
    """An assessment and everything needed to reproduce or contest it."""

    __tablename__ = "assessments"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    rubric_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    rubric_version: Mapped[int] = mapped_column(nullable=False)
    judgments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    assessed_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retrieval_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redaction_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped[ApplicationRow] = relationship(lazy="raise")
    rubric: Mapped[RubricRow] = relationship(lazy="raise")

    __table_args__ = (Index("ix_assessments_application_created", "application_id", "created_at"),)


class StageTransitionRow(Base):
    """Append-only history of every move, with who made it and why."""

    __tablename__ = "stage_transitions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    from_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    application: Mapped[ApplicationRow] = relationship(lazy="raise")

    __table_args__ = (Index("ix_stage_transitions_application_at", "application_id", "at"),)


class ScorecardRow(Base):
    __tablename__ = "scorecards"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the application so "which requisitions is this interviewer
    # on?" -- the per-requisition scoping query -- needs no join.
    requisition_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False
    )
    interviewer_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    assigned_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ratings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    recommendation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped[ApplicationRow] = relationship(lazy="raise")
    requisition: Mapped[RequisitionRow] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint(
            "application_id", "interviewer_id", name="uq_scorecards_application_interviewer"
        ),
        Index("ix_scorecards_interviewer", "tenant_id", "interviewer_id"),
    )
