"""Phase 0 — real accounts, RBAC, anonymous identity fix, complaint IDs

Revision ID: 1759475118ab
Revises: fa3396ab26bc
Create Date: 2026-09-12

Additive-only migration (docs/superpowers/specs/2026-09-12-phase0-foundations-design.md).
No existing rows are deleted. The old citizen_id sentinel string ("anon",
"actor-citizen-in", "demo-citizen-in-1", ...) is preserved by moving it into
the new anonymous_token column for every existing row — citizen_accounts
does not exist yet at migration time (seed/seed_data.py creates real
accounts afterward), so pre-Phase-0 rows stay attributed as anonymous. This
is fine: they are seed/demo data, explicitly allowed to stay demo-quality.
"""
import secrets
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '1759475118ab'
down_revision = 'fa3396ab26bc'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # -----------------------------------------------------------------
    # 1. New tables
    # -----------------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("country_id", sa.String(36), sa.ForeignKey("countries.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.UniqueConstraint("country_id", "code", name="uq_department_country_code"),
    )

    op.create_table(
        "citizen_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("country_id", sa.String(36), sa.ForeignKey("countries.id"), nullable=False),
        sa.Column("preferred_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("consent_given_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_citizen_accounts_email", "citizen_accounts", ["email"])

    government_role_enum = sa.Enum(
        "national_admin", "state_admin", "district_officer",
        "department_officer", "analyst", "reviewer",
        name="government_role_enum",
    )

    op.create_table(
        "government_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", government_role_enum, nullable=False),
        sa.Column("country_id", sa.String(36), sa.ForeignKey("countries.id"), nullable=False),
        sa.Column("region_id", sa.String(36), sa.ForeignKey("administrative_regions.id"), nullable=True),
        sa.Column("department_id", sa.String(36), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("provisioned_by", sa.String(36), sa.ForeignKey("government_accounts.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_government_accounts_email", "government_accounts", ["email"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_type", sa.Enum("citizen", "government", name="reset_account_type_enum"), nullable=False),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_password_reset_tokens_account_id", "password_reset_tokens", ["account_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_type", sa.Enum("citizen", "government", "system", "anonymous", name="audit_actor_type_enum"), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])

    # -----------------------------------------------------------------
    # 2. Expand report_status_enum (Phase 0 §6). Must run outside the
    #    surrounding DDL that references the new labels in the same
    #    transaction — adding the labels here and using them only in later,
    #    separate application transactions is safe on Postgres 12+.
    # -----------------------------------------------------------------
    for value in ("Submitted", "Processing", "Verified", "UnderReview", "Actioned", "Resolved", "Closed"):
        op.execute(f"ALTER TYPE report_status_enum ADD VALUE IF NOT EXISTS '{value}'")

    for value in (
        "national_admin", "state_admin", "district_officer",
        "department_officer", "analyst", "reviewer",
    ):
        op.execute(f"ALTER TYPE decision_actor_role_enum ADD VALUE IF NOT EXISTS '{value}'")

    # -----------------------------------------------------------------
    # 3. reports — complaint_id, real identity columns, consent
    # -----------------------------------------------------------------
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("complaint_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("citizen_account_id", sa.String(36), sa.ForeignKey("citizen_accounts.id"), nullable=True))
        batch_op.add_column(sa.Column("anonymous_token", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("consent_given_at", sa.DateTime(timezone=True), nullable=True))

    _backfill_identity_and_complaint_id(bind)

    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.alter_column("complaint_id", nullable=False)
        batch_op.create_unique_constraint("uq_reports_complaint_id", ["complaint_id"])
        batch_op.create_index("ix_reports_complaint_id", ["complaint_id"])
        batch_op.create_index("ix_reports_citizen_account_id", ["citizen_account_id"])
        batch_op.create_index("ix_reports_anonymous_token", ["anonymous_token"])
        batch_op.drop_column("citizen_id")

    # -----------------------------------------------------------------
    # 4. contributions / verifications — same identity-column swap
    # -----------------------------------------------------------------
    for table in ("contributions", "verifications"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("citizen_account_id", sa.String(36), sa.ForeignKey("citizen_accounts.id"), nullable=True))
            batch_op.add_column(sa.Column("anonymous_token", sa.String(64), nullable=True))

    _backfill_contribution_verification_identity(bind, "contributions")
    _backfill_contribution_verification_identity(bind, "verifications")

    for table in ("contributions", "verifications"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_index(f"ix_{table}_citizen_account_id", ["citizen_account_id"])
            batch_op.create_index(f"ix_{table}_anonymous_token", ["anonymous_token"])
            batch_op.drop_column("citizen_id")

    # -----------------------------------------------------------------
    # 5. government_decisions — decided_by_account_id
    # -----------------------------------------------------------------
    with op.batch_alter_table("government_decisions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("decided_by_account_id", sa.String(36), sa.ForeignKey("government_accounts.id"), nullable=True))

    # -----------------------------------------------------------------
    # 6. projects — assigned_officer_id, department_id
    # -----------------------------------------------------------------
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assigned_officer_id", sa.String(36), sa.ForeignKey("government_accounts.id"), nullable=True))
        batch_op.add_column(sa.Column("department_id", sa.String(36), sa.ForeignKey("departments.id"), nullable=True))


def _backfill_identity_and_complaint_id(bind):
    rows = bind.execute(sa.text("SELECT id, citizen_id, country_id, created_at FROM reports")).fetchall()
    country_codes = {
        r[0]: r[1] for r in bind.execute(sa.text("SELECT id, code FROM countries")).fetchall()
    }
    for report_id, citizen_id, country_id, created_at in rows:
        anonymous_token = citizen_id or uuid.uuid4().hex
        year = (created_at or datetime.now(timezone.utc)).year
        code = country_codes.get(country_id, "IN")
        complaint_id = f"NEVO-{code}-{year}-{secrets.token_hex(4).upper()}"
        bind.execute(
            sa.text(
                "UPDATE reports SET anonymous_token = :tok, complaint_id = :cid WHERE id = :id"
            ),
            {"tok": anonymous_token, "cid": complaint_id, "id": report_id},
        )


def _backfill_contribution_verification_identity(bind, table):
    rows = bind.execute(sa.text(f"SELECT id, citizen_id FROM {table}")).fetchall()
    for row_id, citizen_id in rows:
        anonymous_token = citizen_id or uuid.uuid4().hex
        bind.execute(
            sa.text(f"UPDATE {table} SET anonymous_token = :tok WHERE id = :id"),
            {"tok": anonymous_token, "id": row_id},
        )


def downgrade():
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("department_id")
        batch_op.drop_column("assigned_officer_id")

    with op.batch_alter_table("government_decisions", schema=None) as batch_op:
        batch_op.drop_column("decided_by_account_id")

    for table in ("contributions", "verifications"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("citizen_id", sa.String(36), nullable=True))
        op.execute(f"UPDATE {table} SET citizen_id = COALESCE(citizen_account_id, anonymous_token)")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("citizen_id", nullable=False)
            batch_op.drop_index(f"ix_{table}_citizen_account_id")
            batch_op.drop_index(f"ix_{table}_anonymous_token")
            batch_op.drop_column("citizen_account_id")
            batch_op.drop_column("anonymous_token")

    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("citizen_id", sa.String(36), nullable=True))
    op.execute("UPDATE reports SET citizen_id = COALESCE(citizen_account_id, anonymous_token, 'anon')")
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.alter_column("citizen_id", nullable=False)
        batch_op.drop_index("ix_reports_complaint_id")
        batch_op.drop_index("ix_reports_citizen_account_id")
        batch_op.drop_index("ix_reports_anonymous_token")
        batch_op.drop_constraint("uq_reports_complaint_id", type_="unique")
        batch_op.drop_column("complaint_id")
        batch_op.drop_column("citizen_account_id")
        batch_op.drop_column("anonymous_token")
        batch_op.drop_column("consent_given_at")

    op.drop_table("audit_log")
    op.drop_table("password_reset_tokens")
    op.drop_table("government_accounts")
    op.drop_table("citizen_accounts")
    op.drop_table("departments")

    sa.Enum(name="government_role_enum").drop(op.get_bind(), checkfirst=True)
    # Note: Postgres cannot drop individual enum VALUES (report_status_enum /
    # decision_actor_role_enum keep their added labels on downgrade — this is
    # a one-way expansion, consistent with the additive-only migration policy).
