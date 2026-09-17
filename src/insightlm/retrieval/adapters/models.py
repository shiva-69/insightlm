"""Persistence models for retrieval: chunks, their vectors, and answered questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from insightlm.retrieval.adapters.embedding import EMBEDDING_DIMENSIONS
from insightlm.shared.adapters.db.base import Base


class ChunkRow(Base):
    """A retrievable unit, its geometry, and both index representations.

    Vector and full-text live on the same row as the metadata, which is the whole
    argument for pgvector over a separate vector database (ADR-0002): a chunk and its
    embedding commit in one transaction, tenant filtering happens inside the SQL, and
    erasure is a single DELETE rather than a distributed saga.
    """

    __tablename__ = "chunks"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    context_header: Mapped[str] = mapped_column(Text, nullable=False, default="")
    section_path: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    char_start: Mapped[int] = mapped_column(nullable=False)
    char_end: Mapped[int] = mapped_column(nullable=False)
    # Merged per-line rectangles: a handful per chunk, not one per character.
    rects: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)

    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Maintained by a database trigger, so it can never drift from `body`.
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # No `relationship()` to DocumentVersionRow, and therefore no import of another
    # module's persistence models. The foreign key is declared by table name, which
    # needs no Python import; the relationship would only have mattered for flush
    # ordering, and chunks are always written in a *later* transaction than the
    # version they belong to -- indexing happens in the worker, after upload commits.
    # Keeping it out is what lets `import-linter` hold the line between the modules.

    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_chunks_version_ordinal"),
        Index("ix_chunks_tenant_version", "tenant_id", "document_version_id"),
        # Declared here rather than only in the migration so `alembic check` can see
        # them. An index created by raw SQL is invisible to autogenerate, which then
        # proposes dropping it on the next migration -- a silent performance cliff.
        Index(
            "ix_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        # HNSW with cosine distance, matching the L2-normalised vectors the embedders
        # produce. m / ef_construction are pgvector defaults; tuning them is a Phase 3
        # measurement, not a guess.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class AnswerRow(Base):
    """An answered question, with everything needed to reproduce it.

    `retrieval_config_hash` plus the model and prompt versions make "would this answer
    differ under the new pipeline?" a runnable experiment rather than a guess -- which
    is what RQ2 and RQ3 need, and what an auditor asks for in Phase 6.

    Claims are stored as JSONB rather than as rows. They are an immutable snapshot of
    what was said and what it cited, never queried relationally, and normalising them
    would buy nothing but joins.
    """

    __tablename__ = "answers"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    asked_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    claims: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)

    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_answers_tenant_created", "tenant_id", "created_at"),)
