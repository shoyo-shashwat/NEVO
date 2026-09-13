"""reconcile untracked auth schema (citizen_accounts, government_accounts, departments, audit_log, password_reset_tokens)

Revision ID: 1759475118ab
Revises: fa3396ab26bc
Create Date: 2026-09-13 00:00:00.000000

PROVENANCE: this revision id is not a typo — it is the actual value already
recorded in the project's alembic_version table on the shared Neon database.
Someone applied this schema (citizen_accounts, government_accounts,
departments, audit_log, password_reset_tokens — with real data: 3 citizen
accounts, 10 government accounts, 5 departments, 118 audit log rows) to that
database using application code that was never committed to this git repo.

This migration file's upgrade()/downgrade() are deliberately no-ops. Its
only purpose is to give Alembic's migration graph a revision matching what
the live database already has, so `flask db current` / `flask db migrate`
work correctly going forward instead of failing with "Can't locate revision
identified by '1759475118ab'". Do NOT add real DDL here — the schema this
revision represents already exists. Schema changes on top of it belong in
the next migration (see the one immediately following this one in
migrations/versions/).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1759475118ab'
down_revision = 'fa3396ab26bc'
branch_labels = None
depends_on = None


def upgrade():
    # No-op — see module docstring. The tables this revision represents
    # (citizen_accounts, government_accounts, departments, audit_log,
    # password_reset_tokens) already exist on every database that reports
    # this as its current revision.
    pass


def downgrade():
    # Deliberately not implemented — downgrading past this point would
    # drop tables containing real account and audit data. If you actually
    # need to remove this schema, do it explicitly and deliberately, not
    # via `flask db downgrade`.
    raise NotImplementedError(
        "Refusing to auto-downgrade past the untracked-schema reconciliation "
        "point — citizen_accounts/government_accounts/departments/audit_log/"
        "password_reset_tokens contain real data. Drop tables explicitly if "
        "that is genuinely intended."
    )
