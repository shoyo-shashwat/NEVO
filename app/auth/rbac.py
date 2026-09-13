# auth/rbac.py
#
# Role + scope enforcement — replaces the session-role check that used to
# live in auth/session.py. This is the one seam scope logic goes through;
# routes call these helpers instead of re-implementing "what can this
# account see" themselves (Phase 0 design §2).
#
# current_user (Flask-Login) is either a GovernmentAccount, a CitizenAccount,
# or flask_login.AnonymousUserMixin when nobody is logged in.

from functools import wraps

from flask import redirect, url_for, flash, abort
from flask_login import current_user

from app.models.accounts import (
    DECISION_AUTHORITY_ROLES,
    IMPLEMENTATION_ROLES,
    EVIDENCE_ONLY_ROLES,
)


def current_government_account():
    """Returns the logged-in GovernmentAccount, or None."""
    if current_user.is_authenticated and current_user.get_id().startswith("gov:"):
        return current_user
    return None


def current_citizen_account():
    """Returns the logged-in CitizenAccount, or None (citizen may be anonymous)."""
    if current_user.is_authenticated and current_user.get_id().startswith("citizen:"):
        return current_user
    return None


def require_government_role(*roles: str):
    """
    Route decorator — redirects to government login if not authenticated as
    one of the given government roles. Does NOT enforce geographic/department
    scope by itself; combine with scoped_region_ids()/scoped_department_ids()
    inside the view for queries.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            account = current_government_account()
            if account is None:
                flash("Please sign in with a government account to continue.", "info")
                return redirect(url_for("auth.government_login"))
            if account.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_decision_authority(f):
    """Gate for routes that issue a GovernmentDecision (Prioritize/Defer/...)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        account = current_government_account()
        if account is None:
            flash("Please sign in with a government account to continue.", "info")
            return redirect(url_for("auth.government_login"))
        if account.role not in DECISION_AUTHORITY_ROLES:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def require_implementation_authority(f):
    """Gate for routes that manage Projects/Outcomes."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        account = current_government_account()
        if account is None:
            flash("Please sign in with a government account to continue.", "info")
            return redirect(url_for("auth.government_login"))
        if account.role not in IMPLEMENTATION_ROLES:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Scope enforcement — the actual server-side filtering, not just a gate.
# ---------------------------------------------------------------------------

def scoped_region_ids(account) -> list[str] | None:
    """
    Returns the list of AdministrativeRegion ids this account may act on, or
    None to mean "unscoped" (national_admin, or an evidence-only role with
    no region set — analysts/reviewers are read-scoped the same as any other
    non-national role once they have a region assigned).

    Callers filter their query by `.filter(Model.region_id.in_(ids))` only
    when this returns a list; a None return means skip the filter entirely.
    Includes the account's own region plus all of its descendant regions
    (a state_admin sees their state's districts too), computed from
    AdministrativeRegion.parent_region_id.
    """
    if account is None:
        return []  # no account, no access — caller decides what "no rows" means
    if account.role == "national_admin" or account.region_id is None:
        return None

    from app.extensions import db
    from app.models.shared import AdministrativeRegion

    ids = {account.region_id}
    frontier = [account.region_id]
    while frontier:
        children = (
            db.session.query(AdministrativeRegion.id)
            .filter(AdministrativeRegion.parent_region_id.in_(frontier))
            .all()
        )
        child_ids = [c.id for c in children if c.id not in ids]
        ids.update(child_ids)
        frontier = child_ids
    return list(ids)


def scoped_department_ids(account) -> list[str] | None:
    """
    Returns [account.department_id] if department-scoped, or None if not
    department-restricted (most roles aren't; only department_officer is).
    """
    if account is None:
        return []
    if account.role == "department_officer" and account.department_id:
        return [account.department_id]
    return None


def is_evidence_only(account) -> bool:
    return account is not None and account.role in EVIDENCE_ONLY_ROLES
