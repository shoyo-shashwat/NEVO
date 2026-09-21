"""mplads ledger: term-based fields (recommended, work counts), drop released

Revision ID: c92d5f18e3a7
Revises: b41c7e2d9a10
Create Date: 2026-09-21 15:00:00

Why: the official portal (mplads.mospi.gov.in) publishes cumulative-for-the-term
figures with no "released" amount and no financial-year split. The table was
created empty one revision ago and nothing reads `released`, so reshaping it
loses no data.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c92d5f18e3a7'
down_revision = 'b41c7e2d9a10'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('uq_mplads_ledger_region_fy', 'mplads_ledgers', type_='unique')
    op.alter_column('mplads_ledgers', 'financial_year', new_column_name='period_label',
                    existing_type=sa.String(length=9), type_=sa.String(length=60))
    op.create_unique_constraint('uq_mplads_ledger_region_period', 'mplads_ledgers',
                                ['region_id', 'period_label'])
    op.drop_column('mplads_ledgers', 'released')
    op.add_column('mplads_ledgers', sa.Column('recommended', sa.Numeric(precision=18, scale=2), nullable=True))
    op.add_column('mplads_ledgers', sa.Column('works_recommended', sa.Integer(), nullable=True))
    op.add_column('mplads_ledgers', sa.Column('works_sanctioned', sa.Integer(), nullable=True))
    op.add_column('mplads_ledgers', sa.Column('works_completed', sa.Integer(), nullable=True))
    op.add_column('mplads_ledgers', sa.Column('term_end', sa.Date(), nullable=True))
    op.add_column('mplads_ledgers', sa.Column('notes', sa.Text(), nullable=True))


def downgrade():
    for col in ('notes', 'term_end', 'works_completed', 'works_sanctioned', 'works_recommended', 'recommended'):
        op.drop_column('mplads_ledgers', col)
    op.add_column('mplads_ledgers', sa.Column('released', sa.Numeric(precision=18, scale=2), nullable=True))
    op.drop_constraint('uq_mplads_ledger_region_period', 'mplads_ledgers', type_='unique')
    op.alter_column('mplads_ledgers', 'period_label', new_column_name='financial_year',
                    existing_type=sa.String(length=60), type_=sa.String(length=9))
    op.create_unique_constraint('uq_mplads_ledger_region_fy', 'mplads_ledgers',
                                ['region_id', 'financial_year'])
