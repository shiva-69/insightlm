"""Persistence models for the governance module."""

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


class AuditEntryRow(Base):
    """Append-only, hash-chained.

    ``sequence`` is a per-tenant monotonic counter. It gives a deterministic ordering
    for chain verification that does not depend on timestamp resolution -- two entries
    written in the same microsecond would otherwise be unorderable, and an ambiguous
    order makes the chain unverifiable.

    The migration additionally installs a BEFORE UPDATE OR DELETE trigger that
    raises, so append-only is enforced by the database rather than merely by the
    absence of a repository method. A trigger, not a GRANT: this deployment connects
    as the table owner, and a GRANT does not constrain an owner.
    """

    __tablename__ = "audit_entries"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    # Deliberately NOT foreign keys. An audit log must outlive what it describes: an
    # erasure request deletes the user, and the entry proving the erasure happened has
    # to survive it intact. A FK here would force a choice between blocking erasure
    # (RESTRICT) and destroying the evidence (SET NULL) -- both wrong. Keeping them as
    # plain identifiers also keeps `governance` independent of `identity`, which is what
    # lets every module audit without the audit module depending on any of them.
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence", name="uq_audit_entries_tenant_sequence"),
        UniqueConstraint("tenant_id", "entry_hash", name="uq_audit_entries_tenant_entry_hash"),
        Index("ix_audit_entries_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_audit_entries_subject", "tenant_id", "subject_type", "subject_id"),
    )


class PromptTemplateRow(Base):
    """A named prompt. Versions are immutable rows beneath it."""

    __tablename__ = "prompt_templates"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromptVersionRow(Base):
    """Content-hashed and immutable.

    Editing a prompt creates a new version; it never mutates history. Every AI output
    references the version that produced it, which is what makes "reproduce this
    assessment" a real operation rather than a claim.
    """

    __tablename__ = "prompt_versions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    template_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("prompt_templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    template: Mapped[PromptTemplateRow] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_prompt_versions_template_version"),
        UniqueConstraint("content_hash", name="uq_prompt_versions_content_hash"),
    )


class ModelVersionRow(Base):
    """The registry of models the system is permitted to call.

    Pinning matters for reproducibility: "gpt-4o" is a moving target, a dated snapshot
    is not.
    """

    __tablename__ = "model_versions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("provider", "model_id", "purpose", name="uq_model_versions_identity"),
    )
