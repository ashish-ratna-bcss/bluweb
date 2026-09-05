"""phase 9 capability learning: discovery status + extraction quality

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_with_backfill(table: str, column: sa.Column, default_sql: str) -> None:
    """Same safe-add pattern as migration 0008: add nullable, backfill
    existing rows, then tighten to NOT NULL."""
    was_nullable = column.nullable
    column.nullable = True
    op.add_column(table, column)
    op.execute(f"UPDATE {table} SET {column.name} = {default_sql} WHERE {column.name} IS NULL")
    if not was_nullable:
        op.alter_column(table, column.name, nullable=False)


def upgrade() -> None:
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('sitemap_status', sa.String(length=16), nullable=False), "'unknown'")
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('feed_status', sa.String(length=16), nullable=False), "'unknown'")
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('sitemap_url_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('feed_url_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('avg_extraction_quality', sa.Float(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('quality_observations', sa.Integer(), nullable=False), '0')

    _add_column_with_backfill('url_pattern_stats', sa.Column('avg_extraction_quality', sa.Float(), nullable=False), '0')
    _add_column_with_backfill('url_pattern_stats', sa.Column('quality_observations', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('url_pattern_stats', sa.Column('pagination_detected_count', sa.Integer(), nullable=False), '0')


def downgrade() -> None:
    op.drop_column('url_pattern_stats', 'pagination_detected_count')
    op.drop_column('url_pattern_stats', 'quality_observations')
    op.drop_column('url_pattern_stats', 'avg_extraction_quality')

    op.drop_column('fetch_strategy_stats', 'quality_observations')
    op.drop_column('fetch_strategy_stats', 'avg_extraction_quality')
    op.drop_column('fetch_strategy_stats', 'feed_url_count')
    op.drop_column('fetch_strategy_stats', 'sitemap_url_count')
    op.drop_column('fetch_strategy_stats', 'feed_status')
    op.drop_column('fetch_strategy_stats', 'sitemap_status')
