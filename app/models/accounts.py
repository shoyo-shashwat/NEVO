# models/accounts.py
#
# Real identity — replaces the session-only demo-actor registry that used
# to live entirely in app/auth/session.py (Phase 0 / docs/superpowers/specs/
# 2026-09-12-phase0-foundations-design.md).
#
# Two separate tables rather than one polymorphic `users` table: citizen and
# government accounts have genuinely different lifecycles (self-signup +
# consent vs. admin-provisioned + role/scope) and forcing them into one
# table would produce a pile of mutually-exclusive nullable columns.
#
# Password hashing lives in app/auth/security.py, never inline here.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Department — needed for department_officer scoping and "assign
# responsible department" (Government MVP §7 / §12).
# ---------------------------------------------------------------------------

class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(50), nullable=False)  # e.g. "health", "pwd", "education"

    __table_args__ = (
        db.UniqueConstraint("country_id", "code", name="uq_department_country_code"),
    )

    country = db.relationship("Country", foreign_keys=[country_id])

    def __repr__(self):
        return f"<Department {self.code} ({self.country_id})>"


# ---------------------------------------------------------------------------
# CitizenAccount
# ---------------------------------------------------------------------------

class CitizenAccount(db.Model):
    """
    Real citizen identity. Optional — anonymous submission is still fully
    supported (see Report.anonymous_token) and this table is never required
    to submit a report.
    """
    __tablename__ = "citizen_accounts"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    preferred_language = db.Column(db.String(10), nullable=False, default="en")

    # Consent (Government/Citizen spec §15 — consent cannot be skipped)
    consent_given_at = db.Column(db.DateTime(timezone=True), nullable=False)
    consent_version = db.Column(db.String(20), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    country = db.relationship("Country", foreign_keys=[country_id])

    # Flask-Login integration — see app/auth/security.py Principal wrapper.
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active_account(self):
        return self.is_active

    def get_id(self):
        return f"citizen:{self.id}"

    def __repr__(self):
        return f"<CitizenAccount {self.email}>"


# ---------------------------------------------------------------------------
# GovernmentAccount
# ---------------------------------------------------------------------------

GOVERNMENT_ROLES = (
    "national_admin",
    "state_admin",
    "district_officer",
    "department_officer",
    "analyst",
    "reviewer",
)

# Roles that may issue a GovernmentDecision (Prioritize/Defer/Deprioritize/...).
# Mirrors the old mp+planning_officer decision authority, split by scope.
DECISION_AUTHORITY_ROLES = ("national_admin", "state_admin", "district_officer")

# Roles that may propose/update Projects and record Outcomes.
IMPLEMENTATION_ROLES = ("national_admin", "state_admin", "district_officer", "department_officer")

# Roles restricted to the evidence side only — never decisions, never projects.
EVIDENCE_ONLY_ROLES = ("analyst", "reviewer")


class GovernmentAccount(db.Model):
    """
    Admin-provisioned only — there is no public signup route for government
    roles (Phase 0 design decision §3). provisioned_by records which
    account created this one; NULL only for the one-time bootstrap admin.
    """
    __tablename__ = "government_accounts"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)

    role = db.Column(db.Enum(*GOVERNMENT_ROLES, name="government_role_enum"), nullable=False)

    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    # NULL region_id = unscoped (only valid for national_admin).
    region_id = db.Column(db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True)
    # NULL department_id = not department-scoped.
    department_id = db.Column(db.String(36), db.ForeignKey("departments.id"), nullable=True)

    provisioned_by = db.Column(db.String(36), db.ForeignKey("government_accounts.id"), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    department = db.relationship("Department", foreign_keys=[department_id])
    provisioner = db.relationship("GovernmentAccount", remote_side=[id])

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return f"gov:{self.id}"

    def has_decision_authority(self) -> bool:
        return self.role in DECISION_AUTHORITY_ROLES

    def has_implementation_authority(self) -> bool:
        return self.role in IMPLEMENTATION_ROLES

    def is_evidence_only(self) -> bool:
        return self.role in EVIDENCE_ONLY_ROLES

    def __repr__(self):
        return f"<GovernmentAccount {self.email} role={self.role}>"


# ---------------------------------------------------------------------------
# PasswordResetToken
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
# AuditLog — flat table, not event sourcing (Phase 0 design decision §7).
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    actor_type = db.Column(
        db.Enum("citizen", "government", "system", "anonymous", name="audit_actor_type_enum"),
        nullable=False,
    )
    actor_id = db.Column(db.String(36), nullable=True)  # NULL for actor_type='system'/'anonymous'
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(36), nullable=True)
    before_state = db.Column(db.JSON, nullable=True)
    after_state = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )

    def __repr__(self):
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"
