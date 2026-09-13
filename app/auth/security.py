# auth/security.py
#
# Password hashing, anonymous-identity cookie handling, and complaint-ID
# generation. Framework-light (Flask request/response only) — no direct
# SQLAlchemy session use, so it stays easy to unit test.
#
# Phase 0 design (docs/superpowers/specs/2026-09-12-phase0-foundations-design.md
# §4, §5, §8).

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone

from flask import request
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# Password hashing — Werkzeug's PBKDF2, already a transitive Flask
# dependency. No new crypto library needed (Phase 0 design §8).
# ---------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    return generate_password_hash(plain_password, method="pbkdf2:sha256:600000")


def verify_password(password_hash: str, plain_password: str) -> bool:
    return check_password_hash(password_hash, plain_password)


# ---------------------------------------------------------------------------
# Password reset tokens — the token itself is never stored; only its hash
# is, so a leaked database dump can't be used to reset accounts.
# ---------------------------------------------------------------------------

def generate_reset_token() -> tuple[str, str]:
    """Returns (raw_token_for_email, sha256_hash_for_storage)."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Anonymous identity cookie (Phase 0 design §4).
#
# A citizen who never signs up still gets a stable, unguessable identifier
# so COUNT(DISTINCT ...) on DemandCluster.unique_contributors reflects real
# distinct people instead of collapsing every anonymous submitter platform-
# wide into one bucket (the bug in the old "anon" sentinel string).
#
# This value is NOT an account, carries no PII, is never shown to anyone
# (including the citizen it belongs to), and is never joined to a real
# identity. It exists purely as an internal counting/tracking key.
# ---------------------------------------------------------------------------

ANONYMOUS_COOKIE_NAME = "nevo_anon_id"
_ANONYMOUS_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 3  # 3 years


def get_or_create_anonymous_token(response=None) -> tuple[str, "callable | None"]:
    """
    Returns (token, set_cookie_fn). If the incoming request already carries
    the anonymous cookie, reuses it. Otherwise mints a new one and returns a
    function the caller must invoke with the outgoing Flask response so the
    cookie actually gets set — callers must not forget this, since a token
    that's generated but never persisted client-side would mint a new one
    every request and defeat the whole point.
    """
    existing = request.cookies.get(ANONYMOUS_COOKIE_NAME)
    if existing and _looks_like_token(existing):
        return existing, None

    new_token = uuid.uuid4().hex

    def _set_cookie(resp):
        resp.set_cookie(
            ANONYMOUS_COOKIE_NAME,
            new_token,
            max_age=_ANONYMOUS_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
        )
        return resp

    return new_token, _set_cookie


def _looks_like_token(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


# ---------------------------------------------------------------------------
# Complaint ID generation (Phase 0 design §5).
#
# Human-readable, independent of the internal UUID primary key. Format:
#   NEVO-<country-code>-<year>-<8 uppercase hex chars>
# Collision probability is negligible (32 bits of entropy) but callers
# should still retry on a rare unique-constraint violation rather than
# assume success.
# ---------------------------------------------------------------------------

def generate_complaint_id(country_code: str) -> str:
    year = datetime.now(timezone.utc).year
    suffix = secrets.token_hex(4).upper()  # 8 hex chars
    return f"NEVO-{country_code.upper()}-{year}-{suffix}"


# ---------------------------------------------------------------------------
# Consent version — bump when the consent/privacy text materially changes.
# ---------------------------------------------------------------------------

CURRENT_CONSENT_VERSION = "2026-09-v1"
