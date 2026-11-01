"""Contract review: playbooks and reviews, the second vertical's tables

Revision ID: 0010_contract_review
Revises: 0009_assessment_jobs
Create Date: 2026-11-01 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from insightlm.settings import get_settings

revision: str = "0010_contract_review"
down_revision: str | None = "0009_assessment_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("playbooks", "contract_reviews")


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("positions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("proposed_by", sa.UUID(), nullable=False),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbooks")),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_playbooks_name_version"),
    )
    op.create_table(
        "contract_reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("document_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("playbook_id", sa.UUID(), nullable=False),
        sa.Column("playbook_version", sa.Integer(), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("retrieval_config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contract_reviews")),
    )
    op.create_index(
        "ix_contract_reviews_tenant_created",
        "contract_reviews",
        ["tenant_id", "created_at"],
        unique=False,
    )

    role = get_settings().database_app_role
    for table in _TABLES:
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
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{role}"')


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_contract_reviews_tenant_created", table_name="contract_reviews")
    op.drop_table("contract_reviews")
    op.drop_table("playbooks")
