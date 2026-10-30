"""Assessment jobs: assessments requested in a transaction and run by the worker

Revision ID: 0009_assessment_jobs
Revises: 0008_job_updated_at
Create Date: 2026-10-30 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from insightlm.settings import get_settings

revision: str = "0009_assessment_jobs"
down_revision: str | None = "0008_job_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessment_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("requested_role", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.UUID(), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_assessment_jobs_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name=op.f("fk_assessment_jobs_assessment_id_assessments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_jobs")),
    )
    op.create_index(
        "ix_assessment_jobs_application_created",
        "assessment_jobs",
        ["application_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_assessment_jobs_status_updated",
        "assessment_jobs",
        ["status", "updated_at"],
        unique=False,
    )

    op.execute("ALTER TABLE assessment_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assessment_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY assessment_jobs_tenant_isolation ON assessment_jobs
        FOR ALL
        USING (tenant_id = current_tenant_id())
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    role = get_settings().database_app_role
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON assessment_jobs TO "{role}"')


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS assessment_jobs_tenant_isolation ON assessment_jobs")
    op.drop_index("ix_assessment_jobs_status_updated", table_name="assessment_jobs")
    op.drop_index("ix_assessment_jobs_application_created", table_name="assessment_jobs")
    op.drop_table("assessment_jobs")
