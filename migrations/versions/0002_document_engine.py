"""Document engine: versions, extracted text, chunks with vectors, answers.

Revision ID: 0002_document_engine
Revises: 0001_platform_foundation
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from insightlm.retrieval.adapters.embedding import EMBEDDING_DIMENSIONS
from insightlm.settings import get_settings

revision: str = "0002_document_engine"
down_revision: str | None = "0001_platform_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_SCOPED: tuple[str, ...] = (
    "documents",
    "document_versions",
    "document_texts",
    "ingestion_jobs",
    "dead_letters",
    "chunks",
    "answers",
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_tenant_created", "documents", ["tenant_id", "created_at"])

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
        sa.UniqueConstraint("tenant_id", "checksum", name="uq_document_versions_tenant_checksum"),
    )

    op.create_table(
        "document_texts",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "warnings", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_document_texts_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_version_id", name="pk_document_texts"),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column(
            "completed_steps",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_ingestion_jobs_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_jobs"),
        sa.UniqueConstraint(
            "document_version_id", "pipeline_version", name="uq_ingestion_jobs_version_pipeline"
        ),
    )
    op.create_index("ix_ingestion_jobs_status", "ingestion_jobs", ["tenant_id", "status"])

    op.create_table(
        "dead_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("failed_step", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_dead_letters"),
    )
    op.create_index(
        "ix_dead_letters_tenant_unresolved", "dead_letters", ["tenant_id", "created_at"]
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context_header", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "section_path",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column(
            "rects", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_chunks_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_chunks_version_ordinal"),
    )
    op.create_index("ix_chunks_tenant_version", "chunks", ["tenant_id", "document_version_id"])

    op.create_table(
        "answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asked_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "claims", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("retrieval_config_hash", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_answers"),
    )
    op.create_index("ix_answers_tenant_created", "answers", ["tenant_id", "created_at"])

    _install_search_vector_trigger()
    _install_search_indexes()
    _install_row_level_security()
    _grant_application_role()


def _install_search_vector_trigger() -> None:
    """Maintain the full-text vector in the database, not the application.

    A trigger rather than application code because `search_vector` must never drift
    from `body`: any path that writes a chunk -- ingestion, a backfill, a manual fix --
    gets a correct vector without having to remember. The section heading is weighted
    below the body so a heading match does not outrank a real content match.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunks_refresh_search_vector() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.context_header, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(NEW.body, '')), 'A');
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_search_vector
        BEFORE INSERT OR UPDATE OF body, context_header ON chunks
        FOR EACH ROW EXECUTE FUNCTION chunks_refresh_search_vector();
        """
    )


def _install_search_indexes() -> None:
    """Both index arms, built in Phase 1 even though only dense retrieval queries them.

    Indexing lexically now costs nothing and means Phase 3's hybrid ablation is a
    query change rather than a re-ingestion of the whole corpus.
    """
    # Created through op.create_index, not raw SQL, so these match the declarations on
    # ChunkRow exactly and `alembic check` stays clean.
    op.create_index("ix_chunks_search_vector", "chunks", ["search_vector"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def _install_row_level_security() -> None:
    for table in TENANT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
            """
        )


def _grant_application_role() -> None:
    role = get_settings().database_app_role
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_entries FROM "{role}"')


def downgrade() -> None:
    for table in TENANT_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.execute("DROP TRIGGER IF EXISTS chunks_search_vector ON chunks")
    op.execute("DROP FUNCTION IF EXISTS chunks_refresh_search_vector()")

    op.drop_table("answers")
    op.drop_table("chunks")
    op.drop_table("dead_letters")
    op.drop_table("ingestion_jobs")
    op.drop_table("document_texts")
    op.drop_table("document_versions")
    op.drop_table("documents")
