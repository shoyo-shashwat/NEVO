# models/auth_models.py
#
# Real account entities.
#
# IMPORTANT PROVENANCE NOTE: CitizenAccount, GovernmentAccount, Department,
# AuditLog, and PasswordResetToken already existed as live tables (with real
# data — 3 citizen accounts, 10 government accounts, 5 departments, 118
# audit log rows) in the project's Neon database before this module was
# written. That schema was never committed to this git repo, so these
# model classes were authored by reflecting the actual database structure
# (column types, nullability, enums, FKs, indexes) rather than the other
# way around — see migrations/versions for the reconciliation migration.
# Do not "clean up" field choices here without checking the live schema —
# they're deliberately exact matches.
#
# AccountSession is the one genuinely new table in this file: the existing
# schema had no server-side session record, so there was no way to revoke
# a session before its cookie expired. It follows the same polymorphic
# (account_type, account_id) pattern PasswordResetToken already established
# — one sessions table for both citizen and government accounts, not two.
#
# is_demo is the other addition: a plain boolean on both account tables so
# the existing one-click /login country-card picker (which must not be
# redesigned — see app/auth/session.py) can keep working against real
# accounts without a bespoke actor registry. The 13 pre-existing accounts
# are marked is_demo=True by the reconciliation migration/seed.

import uuid
from datetime import datetime, timezone, timedelta

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


def _default_expiry_30_days():
    return datetime.now(timezone.utc) + timedelta(days=30)


# ---------------------------------------------------------------------------
# CitizenAccount
# ---------------------------------------------------------------------------

class CitizenAccount(db.Model):
    __tablename__ = "citizen_accounts"
    __table_args__ = (
        db.UniqueConstraint("email", name="citizen_accounts_email_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    email = db.Column(db.String(320), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)

    preferred_language = db.Column(db.String(10), nullable=False, default="en")

    # Consent is mandatory at signup — never nullable. consent_version lets
    # a future privacy-policy revision distinguish who consented to what.
    consent_given_at = db.Column(db.DateTime(timezone=True), nullable=False)
    consent_version = db.Column(db.String(20), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    country = db.relationship("Country", foreign_keys=[country_id])

    def __repr__(self):
        return f"<CitizenAccount {self.email}>"


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------

class Department(db.Model):
    __tablename__ = "departments"
    __table_args__ = (
        db.UniqueConstraint("country_id", "code", name="uq_department_country_code"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(50), nullable=False)

    country = db.relationship("Country", foreign_keys=[country_id])

    def __repr__(self):
        return f"<Department {self.code} ({self.country_id})>"


# ---------------------------------------------------------------------------
# GovernmentAccount
# ---------------------------------------------------------------------------

GOVERNMENT_ROLES = (
    "national_admin", "state_admin", "district_officer",
    "department_officer", "analyst", "reviewer",
)

# Maps the fine-grained job role (what's actually stored) onto the coarse
# permission group the existing dashboard/decision/project routes already
# gate on. This is a deliberate compatibility shim, not the final RBAC
# design — see the Production Gap Analysis "P1: finer-grained RBAC" item.
#
# Deliberately 1:1 per country to avoid the collision the original mapping
# had: state_admin and district_officer both mapping to "mp" meant the
# one-click demo picker's "MP" card and "Planning Officer" card could show
# an arbitrary, silently-collapsed account per country (whichever query
# order happened to return last) — and because only India had a
# department_officer account, Brazil/Russia showed no Planning Officer at
# all. Every country's seed data already has one state_admin (-> mp) and
# one district_officer (-> planning_officer), so this mapping alone gives
# all three countries a distinct, correct account under each card with no
# seed changes needed. department_officer moves to "reviewer" (read-only)
# instead of doubling up on planning_officer for India specifically —
# a specialised single-department officer reviewing evidence, not holding
# full cross-department planning/decision authority, is the more accurate
# real-world fit anyway.
#
# decision_maker -> can Prioritize/Defer/Deprioritize (session role "mp")
# operational    -> can propose projects / update status / record outcomes
#                   (session role "planning_officer")
# reviewer_only  -> read access to dashboard/evidence, no write actions
# admin          -> technical configuration + provisioning
ROLE_PERMISSION_GROUP = {
    "national_admin": "admin",
    "state_admin": "mp",
    "district_officer": "planning_officer",
    "department_officer": "reviewer",
    "analyst": "reviewer",
    "reviewer": "reviewer",
}


class GovernmentAccount(db.Model):
    __tablename__ = "government_accounts"
    __table_args__ = (
        db.UniqueConstraint("email", name="government_accounts_email_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    email = db.Column(db.String(320), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)

    role = db.Column(db.Enum(*GOVERNMENT_ROLES, name="government_role_enum"), nullable=False)

    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )
    department_id = db.Column(db.String(36), db.ForeignKey("departments.id"), nullable=True)

    # Self-referencing — which admin provisioned this account. No
    # self-registration path exists for government roles; this is always
    # set except for accounts created by an initial seed/bootstrap.
    provisioned_by = db.Column(db.String(36), db.ForeignKey("government_accounts.id"), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    department = db.relationship("Department", foreign_keys=[department_id])
    provisioned_by_account = db.relationship("GovernmentAccount", remote_side=[id])

    @property
    def permission_group(self) -> str:
        """The coarse role (admin/mp/planning_officer/reviewer) existing routes gate on."""
        return ROLE_PERMISSION_GROUP.get(self.role, "reviewer")

    def __repr__(self):
        return f"<GovernmentAccount {self.email} role={self.role}>"


# ---------------------------------------------------------------------------
# PasswordResetToken (polymorphic — citizen or government account)
# ---------------------------------------------------------------------------

class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    account_type = db.Column(db.Enum("citizen", "government", name="reset_account_type_enum"), nullable=False)
    account_id = db.Column(db.String(36), nullable=False, index=True)

    token_hash = db.Column(db.String(255), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<PasswordResetToken {self.account_type}:{self.account_id}>"


# ---------------------------------------------------------------------------
# AccountSession — NEW table: server-side, revocable sessions.
# ---------------------------------------------------------------------------

class AccountSession(db.Model):
    """
    The DB-backed counterpart to the signed Flask session cookie. The cookie
    carries only an opaque random token; this row is what actually grants
    access, so it can be revoked (logout, password reset, admin action)
    without waiting for cookie expiry. Polymorphic across citizen/government
    accounts, matching PasswordResetToken's existing pattern.
    """
    __tablename__ = "account_sessions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    account_type = db.Column(db.Enum("citizen", "government", name="session_account_type_enum"), nullable=False)
    account_id = db.Column(db.String(36), nullable=False, index=True)

    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_seen_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=_default_expiry_30_days,
    )
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user_agent = db.Column(db.String(300), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)

    def __repr__(self):
        return f"<AccountSession {self.account_type}:{self.account_id} revoked={self.revoked_at is not None}>"


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    actor_type = db.Column(
        db.Enum("citizen", "government", "system", "anonymous", name="audit_actor_type_enum"),
        nullable=False,
    )
    actor_id = db.Column(db.String(36), nullable=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(36), nullable=True)
    before_state = db.Column(db.JSON, nullable=True)
    after_state = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False, index=True,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<AuditLog {self.actor_type}:{self.actor_id} {self.action}>"


def log_action(
    actor_type: str, actor_id: str | None, action: str,
    entity_type: str, entity_id: str | None = None,
    before_state: dict | None = None, after_state: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Convenience helper — appends one AuditLog row. Does not commit."""
    db.session.add(AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
    ))
