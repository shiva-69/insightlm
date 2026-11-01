"""Record whether a document's text was recognised from a scanned image

Revision ID: 0012_document_ocr
Revises: 0011_user_mfa
Create Date: 2026-11-01 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_document_ocr"
down_revision: str | None = "0011_user_mfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_texts",
        sa.Column("ocr_used", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("document_texts", "ocr_used")
