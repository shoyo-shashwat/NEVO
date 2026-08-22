# auth/session.py
#
# Session-based role selector for the BRICS People First MVP.
#
# No OAuth, no login forms, no password hashing (Progress Log §17.6).
# Identity is a seeded demo actor selected from a dropdown.
#
# Session keys (set on role selection):
#   session['role']      — "citizen" | "mp" | "planning_officer" | "admin"
#   session['actor_id']  — the seeded actor's string ID
#   session['actor_name'] — display name for the nav bar
#   session['country_code'] — "IN" | "BR" | "RU"
#
# Usage in routes (guard helper):
#   from app.auth.session import require_role
#
#   @gov_bp.route("/dashboard")
#   @require_role("mp", "planning_officer")
#   def dashboard(): ...
#
# Citizen routes do not require a role (Progress Log §17.6) but will use
# session['actor_id'] when present for "My Timeline".

from functools import wraps
from typing import Optional

from flask import session, redirect, url_for, flash

# ---------------------------------------------------------------------------
# Seeded actor registry
# ---------------------------------------------------------------------------
# Populated by seed/seed_data.py at seed time.
# Keyed by actor_id string — same IDs used in the database seed rows.
# This in-memory dict is the role-selector source of truth for the demo.
# It is rebuilt each time the app starts (seed_data.py writes to DB;
# this dict is reconstructed from those rows by load_demo_actors()).

_DEMO_ACTORS: dict[str, dict] = {}


def load_demo_actors(actors: list[dict]) -> None:
    """
    Populate the in-memory actor registry from seeded DB rows.

    Called once from seed_data.py after rows are committed.
    Each actor dict must have: id, role, name, country_code.

    Simple MVP approach: flat dict, no caching layer.
    """
    _DEMO_ACTORS.clear()
    for a in actors:
        _DEMO_ACTORS[a["id"]] = a


def get_all_demo_actors() -> list[dict]:
    """Return all demo actors sorted by country then role, for the selector UI."""
    order = {"citizen": 0, "mp": 1, "planning_officer": 2, "admin": 3}
    return sorted(
        _DEMO_ACTORS.values(),
        key=lambda a: (a["country_code"], order.get(a["role"], 99)),
    )


def get_actor(actor_id: str) -> Optional[dict]:
    return _DEMO_ACTORS.get(actor_id)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def set_session(actor_id: str) -> bool:
    """
    Write role/actor_id/actor_name/country_code into the Flask session.
    Returns True on success, False if actor_id not found.
    """
    actor = get_actor(actor_id)
    if not actor:
        return False
    session["actor_id"] = actor["id"]
    session["role"] = actor["role"]
    session["actor_name"] = actor["name"]
    session["country_code"] = actor["country_code"]
    return True


def clear_session() -> None:
    for key in ("actor_id", "role", "actor_name", "country_code"):
        session.pop(key, None)


def current_role() -> Optional[str]:
    return session.get("role")


def current_actor_id() -> Optional[str]:
    return session.get("actor_id")


def current_country_code() -> Optional[str]:
    return session.get("country_code")


# ---------------------------------------------------------------------------
# Route guard decorator
# ---------------------------------------------------------------------------

def require_role(*roles: str):
    """
    Decorator that redirects to the role selector if the current session
    role is not in the allowed list.

    Usage:
        @require_role("mp", "planning_officer")
        def dashboard(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") not in roles:
                flash("Please select a role to continue.", "info")
                return redirect(url_for("role_select"))
            return f(*args, **kwargs)
        return wrapper
    return decorator
