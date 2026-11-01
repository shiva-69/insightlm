"""Two-factor authentication: an encrypted authenticator secret per user

Revision ID: 0011_user_mfa
Revises: 0010_contract_review
Create Date: 2026-11-01 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_mfa"
down_revision: str | None = "0010_contract_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_sealed_secret", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("mfa_last_step", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "mfa_last_step")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_sealed_secret")
