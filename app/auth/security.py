# auth/security.py
#
# Password hashing and token generation — no Flask/DB imports, pure
# functions so they're trivially unit-testable.
#
# Passwords: werkzeug's generate_password_hash/check_password_hash (scrypt
# by default in Werkzeug 3.x). Never compare passwords with == — always
# check_password_hash, which does a constant-time comparison.
#
# Tokens (session ids, password-reset links, invite links): a random
# URL-safe string is generated and shown to the user exactly once (in the
# cookie, or in an emailed link). Only its SHA-256 hash is ever persisted —
# a database leak alone cannot be replayed as a live session or valid link.

import re
import secrets
import hashlib

from werkzeug.security import generate_password_hash, check_password_hash

MIN_PASSWORD_LENGTH = 8


def hash_password(raw_password: str) -> str:
    return generate_password_hash(raw_password)


def verify_password(raw_password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, raw_password)


def validate_password_strength(raw_password: str) -> list[str]:
    """
    Returns a list of human-readable problems with the password.
    Empty list means the password is acceptable.

    Deliberately simple (length + letter + digit) rather than an arbitrary
    complexity policy — those are known to push users toward predictable
    substitutions without meaningfully improving security.
    """
    errors = []
    if len(raw_password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if not re.search(r"[A-Za-z]", raw_password):
        errors.append("Password must include at least one letter.")
    if not re.search(r"[0-9]", raw_password):
        errors.append("Password must include at least one number.")
    return errors


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def generate_raw_token() -> str:
    """A random, URL-safe token — used for session ids, reset links, invite links."""
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    """SHA-256 hex digest — deterministic so it can be looked up by hash, not decrypted."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
