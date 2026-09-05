"""final completion: browser compare + completeness learning

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-05 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_with_backfill(table: str, column: sa.Column, default_sql: str) -> None:
    was_nullable = column.nullable
    column.nullable = True
    op.add_column(table, column)
    op.execute(f"UPDATE {table} SET {column.name} = {default_sql} WHERE {column.name} IS NULL")
    if not was_nullable:
        op.alter_column(table, column.name, nullable=False)


def upgrade() -> None:
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('browser_superior_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('http_superior_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('browser_equivalent_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('avg_completeness', sa.Float(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('completeness_observations', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('fetch_strategy_stats', sa.Column('index_children_discovered_total', sa.Integer(), nullable=False), '0')

    _add_column_with_backfill('url_pattern_stats', sa.Column('browser_superior_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('url_pattern_stats', sa.Column('http_superior_count', sa.Integer(), nullable=False), '0')
    _add_column_with_backfill('url_pattern_stats', sa.Column('browser_equivalent_count', sa.Integer(), nullable=False), '0')


def downgrade() -> None:
    op.drop_column('url_pattern_stats', 'browser_equivalent_count')
    op.drop_column('url_pattern_stats', 'http_superior_count')
    op.drop_column('url_pattern_stats', 'browser_superior_count')
    op.drop_column('fetch_strategy_stats', 'index_children_discovered_total')
    op.drop_column('fetch_strategy_stats', 'completeness_observations')
    op.drop_column('fetch_strategy_stats', 'avg_completeness')
    op.drop_column('fetch_strategy_stats', 'browser_equivalent_count')
    op.drop_column('fetch_strategy_stats', 'http_superior_count')
    op.drop_column('fetch_strategy_stats', 'browser_superior_count')
