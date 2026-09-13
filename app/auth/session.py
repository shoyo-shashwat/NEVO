# auth/session.py
#
# Real, DB-backed session management across the two real account tables
# (CitizenAccount, GovernmentAccount) that already existed in the database
# — see app/models/auth_models.py for the provenance note.
#
# How it works:
#   - The Flask session cookie (itself signed by SECRET_KEY) carries only
#     one value: session['sid'], a random opaque token.
#   - load_logged_in_user() runs on every request (before_request hook,
#     wired in app/__init__.py). It hashes session['sid'], looks up the
#     matching AccountSession row, and — if present, not revoked, not
#     expired — loads the CitizenAccount or GovernmentAccount onto flask.g.
#   - Legacy session keys (role, actor_id, actor_name, country_code) are
#     refreshed from that account on every request so existing templates
#     and route code (which read session.get('role') etc. directly) keep
#     working unchanged. session['role'] is the COARSE permission group
#     (citizen | mp | planning_officer | reviewer | admin) that the
#     existing dashboard/decision/project routes already gate on — see
#     GovernmentAccount.permission_group / ROLE_PERMISSION_GROUP for the
#     mapping from the six real job roles onto those four groups.
#   - session['job_title'] carries the real, fine-grained role/title for
#     display (e.g. "District Officer") — base.html prefers it over role.
#
# require_role() keeps its original signature/behaviour: redirect to the
# account picker if the current session's permission group doesn't match.

from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, flash, request, g

from app.extensions import db
from app.auth.security import generate_raw_token, hash_token

_LEGACY_SESSION_KEYS = ("actor_id", "role", "actor_name", "country_code", "job_title", "account_type")

SESSION_LIFETIME_DAYS = 30


# ---------------------------------------------------------------------------
# Request-lifecycle hook — call once per request from app/__init__.py
# ---------------------------------------------------------------------------

def load_logged_in_user() -> None:
    from app.models.auth_models import AccountSession

    g.user = None
    raw_token = session.get("sid")
    if not raw_token:
        return

    token_hash = hash_token(raw_token)
    now = datetime.now(timezone.utc)

    account_session = AccountSession.query.filter_by(token_hash=token_hash).first()
    if (
        account_session is None
        or account_session.revoked_at is not None
        or account_session.expires_at < now
    ):
        _clear_legacy_session()
        session.pop("sid", None)
        return

    account = _load_account(account_session.account_type, account_session.account_id)
    if account is None or not account.is_active:
        _clear_legacy_session()
        session.pop("sid", None)
        return

    g.user = account
    g.account_type = account_session.account_type
    account_session.last_seen_at = now

    # Sync the client-side language choice (i18n.js sets a "nevo_lang"
    # cookie today, unread by the server) onto the real account, so a
    # citizen's language preference follows them across devices/sessions
    # instead of living only in one browser's cookie jar.
    if account_session.account_type == "citizen":
        cookie_lang = request.cookies.get("nevo_lang")
        if cookie_lang and cookie_lang != account.preferred_language:
            account.preferred_language = cookie_lang

    db.session.commit()

    _apply_legacy_session(account, account_session.account_type)


def _load_account(account_type: str, account_id: str):
    from app.models.auth_models import CitizenAccount, GovernmentAccount

    if account_type == "citizen":
        return db.session.get(CitizenAccount, account_id)
    return db.session.get(GovernmentAccount, account_id)


def _apply_legacy_session(account, account_type: str) -> None:
    from app.models.auth_models import GovernmentAccount

    session["actor_id"] = account.id
    session["account_type"] = account_type
    session["country_code"] = account.country.code if account.country else None

    if account_type == "citizen":
        session["role"] = "citizen"
        session["job_title"] = None
        session["actor_name"] = account.full_name or account.email
    else:
        assert isinstance(account, GovernmentAccount)
        session["role"] = account.permission_group
        session["job_title"] = account.role.replace("_", " ").title()
        session["actor_name"] = account.full_name


def _clear_legacy_session() -> None:
    for key in _LEGACY_SESSION_KEYS:
        session.pop(key, None)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def login_user(account, account_type: str) -> None:
    """
    Start a real, DB-backed session for the given account row.

    Rotates the session cookie (session.clear() first) so a pre-login
    session token can never be fixated into a post-login session.
    """
    from app.models.auth_models import AccountSession

    raw_token = generate_raw_token()
    now = datetime.now(timezone.utc)

    db.session.add(AccountSession(
        account_type=account_type,
        account_id=account.id,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(days=SESSION_LIFETIME_DAYS),
        user_agent=(request.headers.get("User-Agent") or "")[:300],
        ip_address=(request.remote_addr or "")[:64],
    ))
    account.last_login_at = now
    db.session.commit()

    session.clear()
    session.permanent = True
    session["sid"] = raw_token
    _apply_legacy_session(account, account_type)


def logout_user() -> None:
    """Revoke the current session row (if any) and clear the cookie."""
    from app.models.auth_models import AccountSession

    raw_token = session.get("sid")
    if raw_token:
        account_session = AccountSession.query.filter_by(token_hash=hash_token(raw_token)).first()
        if account_session and account_session.revoked_at is None:
            account_session.revoked_at = datetime.now(timezone.utc)
            db.session.commit()

    session.clear()
    g.user = None


# ---------------------------------------------------------------------------
# Session read helpers
# ---------------------------------------------------------------------------

def current_user():
    """The real account row (CitizenAccount or GovernmentAccount) for this request, or None."""
    return getattr(g, "user", None)


def current_account_type() -> Optional[str]:
    return session.get("account_type")


def current_role() -> Optional[str]:
    """The coarse permission group — citizen | mp | planning_officer | reviewer | admin."""
    return session.get("role")


def current_actor_id() -> Optional[str]:
    return session.get("actor_id")


def current_country_code() -> Optional[str]:
    return session.get("country_code")


# ---------------------------------------------------------------------------
# Route guard decorators
# ---------------------------------------------------------------------------

def require_role(*roles: str):
    """Decorator that redirects to the account picker if the current session's
    permission group is not in the allowed list."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Please sign in to continue.", "info")
                return redirect(url_for("login_page"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_login(f):
    """Decorator that requires any authenticated account, regardless of role."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.signin", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Demo-actor picker (backs the existing /login country-card UI)
# ---------------------------------------------------------------------------
# Only is_demo=True accounts are reachable here. This is a one-click
# convenience login for seeded reviewer accounts, not a general auth
# bypass — a real citizen/government account (is_demo=False) can never be
# logged into through this path, only through /signin (password) or a
# provisioned government account's password.

def get_all_demo_actors() -> list[dict]:
    """
    All is_demo=True accounts across both tables, shaped as the plain dicts
    the /login and /gov/admin templates already expect
    (id, role, name, country_code) — role here is the COARSE permission
    group (citizen/mp/planning_officer/reviewer/admin) since that's what
    role_select.html's three demo cards (citizen/mp/planning_officer) key
    off of, unchanged from before this schema migration.
    """
    from app.models.auth_models import CitizenAccount, GovernmentAccount

    order = {"citizen": 0, "mp": 1, "planning_officer": 2, "reviewer": 3, "admin": 4}
    actors = []

    for c in CitizenAccount.query.filter_by(is_demo=True, is_active=True).all():
        actors.append({
            "id": c.id, "role": "citizen", "name": c.full_name or c.email,
            "country_code": c.country.code if c.country else "",
            "account_type": "citizen",
        })

    for g_acc in GovernmentAccount.query.filter_by(is_demo=True, is_active=True).all():
        actors.append({
            "id": g_acc.id, "role": g_acc.permission_group, "name": g_acc.full_name,
            "country_code": g_acc.country.code if g_acc.country else "",
            "account_type": "government",
        })

    return sorted(actors, key=lambda a: (a["country_code"], order.get(a["role"], 99)))


def set_demo_session(actor_id: str) -> bool:
    """
    Log in as a seeded demo account by id (searched across both account
    tables). Returns True on success, False if actor_id doesn't match an
    active is_demo=True account.
    """
    from app.models.auth_models import CitizenAccount, GovernmentAccount

    account = db.session.get(CitizenAccount, actor_id)
    if account is not None and account.is_demo and account.is_active:
        login_user(account, "citizen")
        return True

    account = db.session.get(GovernmentAccount, actor_id)
    if account is not None and account.is_demo and account.is_active:
        login_user(account, "government")
        return True

    return False
