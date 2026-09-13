# auth/session.py
#
# Phase 0 rewrite (docs/superpowers/specs/2026-09-12-phase0-foundations-design.md).
# Identity is now real (Flask-Login + CitizenAccount/GovernmentAccount), not
# a session-only demo actor registry. This module keeps the small set of
# helper names citizen/routes.py already calls, so that file needed targeted
# edits rather than a full rewrite — the names stay, the internals don't.
#
# current_role():
#   - a GovernmentAccount's .role (e.g. "district_officer") if signed in as one
#   - "citizen" if signed in as a CitizenAccount
#   - None otherwise (including anonymous citizen browsing — anonymous
#     citizens are real, supported users of the report pipeline, they just
#     have no "role" in the RBAC sense)
#
# current_actor_id():
#   - the signed-in account's id
#   - for a citizen who isn't signed in, a persistent anonymous token (see
#     app/auth/security.py) — never the old literal sentinel string "anon"
#
# current_identity_fields():
#   - {"citizen_account_id": ..., "anonymous_token": ...} — exactly one is
#     set. Use this whenever constructing a Report/Contribution/Verification
#     row, which now have two separate identity columns instead of one
#     merged citizen_id string (Phase 0 design §4).

from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, flash, g

from app.auth.security import get_or_create_anonymous_token
from app.auth.rbac import current_government_account, current_citizen_account


def current_role() -> Optional[str]:
    gov = current_government_account()
    if gov:
        return gov.role
    if current_citizen_account():
        return "citizen"
    return None


def current_actor_id() -> Optional[str]:
    gov = current_government_account()
    if gov:
        return gov.id
    citizen = current_citizen_account()
    if citizen:
        return citizen.id
    return _ensure_anonymous_token()


def current_identity_fields() -> dict:
    citizen = current_citizen_account()
    if citizen:
        return {"citizen_account_id": citizen.id, "anonymous_token": None}
    return {"citizen_account_id": None, "anonymous_token": _ensure_anonymous_token()}


def current_country_code() -> Optional[str]:
    gov = current_government_account()
    if gov and gov.country_id:
        return gov.country.code
    citizen = current_citizen_account()
    if citizen and citizen.country_id:
        return citizen.country.code
    # Anonymous browsing — a lightweight session preference, not identity.
    return session.get("browse_country_code", "IN")


def set_browse_country(country_code: str) -> None:
    """Lets a not-signed-in visitor pick which country's public data to browse."""
    session["browse_country_code"] = country_code.upper()


def _ensure_anonymous_token() -> str:
    """
    Returns this visitor's persistent anonymous token, minting one on first
    visit and queuing it to be set as a cookie on the outgoing response via
    the after_request hook in app/__init__.py (see g._pending_anon_cookie).
    """
    token, set_cookie_fn = get_or_create_anonymous_token()
    if set_cookie_fn is not None:
        g._pending_anon_cookie = set_cookie_fn
    return token


def require_role(*roles: str):
    """
    Generic role gate kept for call sites that don't care whether the
    redirect target is the citizen or government login page. Government
    routes should prefer auth.rbac.require_government_role instead, which
    redirects to the government login specifically and 403s on a wrong role
    rather than bouncing to a generic chooser.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if current_role() not in roles:
                flash("Please sign in to continue.", "info")
                return redirect(url_for("auth.choose_login"))
            return f(*args, **kwargs)
        return wrapper
    return decorator
