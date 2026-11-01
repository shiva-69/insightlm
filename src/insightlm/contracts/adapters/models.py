"""Persistence for contract review.

Positions live on the playbook row and findings on the review row, as JSONB snapshots,
for the reason recruiting gives: both are immutable once written and neither is queried
relationally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from insightlm.shared.adapters.db.base import Base


class PlaybookRow(Base):
    __tablename__ = "playbooks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_playbooks_name_version"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    positions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContractReviewRow(Base):
    __tablename__ = "contract_reviews"
    __table_args__ = (Index("ix_contract_reviews_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # Not a foreign key: documents are the engine's, and a vertical may not reach into
    # another module's tables. A review keeps its own snapshot of the evidence.
    document_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False)
    playbook_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    playbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reviewed_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retrieval_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
