"""Persistence models for the documents module."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
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


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_documents_tenant_created", "tenant_id", "created_at"),)


class DocumentVersionRow(Base):
    """One immutable revision of a document's bytes."""

    __tablename__ = "document_versions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    char_count: Mapped[int | None] = mapped_column(nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[DocumentRow] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        # Dedup: identical bytes re-uploaded in the same tenant resolve to the existing
        # version instead of being parsed and embedded a second time.
        UniqueConstraint("tenant_id", "checksum", name="uq_document_versions_tenant_checksum"),
    )


class DocumentTextRow(Base):
    """Extracted text, one row per version.

    No per-character geometry -- see `ExtractedText` for why. Merged rectangles live
    on the chunk that cites them.
    """

    __tablename__ = "document_texts"

    document_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int] = mapped_column(nullable=False, default=1)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    version: Mapped[DocumentVersionRow] = relationship(lazy="raise")


class IngestionJobRow(Base):
    """Resumable pipeline state.

    `completed_steps` is what makes a retry resume rather than restart: a failure at
    the embed step does not re-parse a 300-page PDF.
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_version: Mapped[int] = mapped_column(nullable=False)
    completed_steps: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    version: Mapped[DocumentVersionRow] = relationship(lazy="raise")

    __table_args__ = (
        # The ingestion idempotency key. Re-uploading the same bytes under the same
        # pipeline version is free; bumping the pipeline version reprocesses on purpose.
        UniqueConstraint(
            "document_version_id",
            "pipeline_version",
            name="uq_ingestion_jobs_version_pipeline",
        ),
        Index("ix_ingestion_jobs_status", "tenant_id", "status"),
    )


class DeadLetterRow(Base):
    """Ingestion work that exhausted its retries.

    A visible table rather than a silent drop: a résumé that failed to parse is a
    candidate who disappears from the pipeline, which is exactly the kind of failure
    that has to be seen to be fixed.
    """

    __tablename__ = "dead_letters"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    failed_step: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_dead_letters_tenant_unresolved", "tenant_id", "created_at"),)
