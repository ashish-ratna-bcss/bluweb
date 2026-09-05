"""create preflight_reports table

Revision ID: 0001
Revises:
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "preflight_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capability_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("report_json", JSONB(), nullable=False),
        sa.Column("crawler_version", sa.String(length=32), nullable=False, server_default="0.1.0"),
        sa.Column(
            "extractor_version", sa.String(length=32), nullable=False, server_default="trafilatura-2.2.0"
        ),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_preflight_reports_normalized_url", "preflight_reports", ["normalized_url"])
    op.create_index("ix_preflight_reports_domain", "preflight_reports", ["domain"])
    op.create_index("ix_preflight_reports_created_at", "preflight_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_preflight_reports_created_at", table_name="preflight_reports")
    op.drop_index("ix_preflight_reports_domain", table_name="preflight_reports")
    op.drop_index("ix_preflight_reports_normalized_url", table_name="preflight_reports")
    op.drop_table("preflight_reports")
