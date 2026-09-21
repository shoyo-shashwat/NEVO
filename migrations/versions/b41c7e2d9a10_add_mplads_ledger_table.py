"""add mplads ledger table

Revision ID: b41c7e2d9a10
Revises: 6833efdb0680
Create Date: 2026-09-21 12:00:00

Additive only: creates one new table, touches nothing existing.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b41c7e2d9a10'
down_revision = '6833efdb0680'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mplads_ledgers',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('region_id', sa.String(length=36), nullable=False),
    sa.Column('seat_label', sa.String(length=200), nullable=False),
    sa.Column('financial_year', sa.String(length=9), nullable=False),
    sa.Column('allocated', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('released', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('sanctioned', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('spent', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('source', sa.String(length=300), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('source_last_updated', sa.Date(), nullable=True),
    sa.Column('platform_last_synced', sa.DateTime(timezone=True), nullable=True),
    sa.Column('method', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['administrative_regions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('region_id', 'financial_year', name='uq_mplads_ledger_region_fy')
    )


def downgrade():
    op.drop_table('mplads_ledgers')
