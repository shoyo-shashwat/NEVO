# auth/routes.py
#
# Real account routes: citizen self-signup, password sign-in, logout,
# account/profile, and password reset — across the two real account tables
# (CitizenAccount, GovernmentAccount) described in app/models/auth_models.py.
#
# Government accounts are never self-registered here — they're created
# directly by an admin from /gov/admin (see government/routes.py::
# provision_government_user), matching the existing schema's
# provisioned_by column and the absence of any invite-token table.
#
# Deliberately separate from the existing "/login" route in app/__init__.py,
# which remains the one-click seeded-demo-account picker (unchanged UI).

from datetime import datetime, timezone, timedelta

from flask import (
    render_template, request, redirect, url_for, flash, session
)

from app.auth import auth_bp
from app.extensions import db, limiter
from app.models.auth_models import (
    CitizenAccount, GovernmentAccount, PasswordResetToken, log_action,
)
from app.models.shared import Country
from app.auth.security import (
    hash_password, verify_password, validate_password_strength,
    is_valid_email, generate_raw_token, hash_token,
)
from app.auth.session import (
    login_user, logout_user, current_user, current_account_type, require_login,
)
from app.services.job_queue import queue_email

RESET_TOKEN_LIFETIME = timedelta(hours=1)
MAX_FAILED_LOGINS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

CONSENT_VERSION = "2026-09-13"  # bump when the privacy policy materially changes


def _client_ip() -> str:
    return (request.remote_addr or "")[:64]


def _email_in_use(email: str) -> bool:
    return (
        CitizenAccount.query.filter_by(email=email).first() is not None
        or GovernmentAccount.query.filter_by(email=email).first() is not None
    )


# ---------------------------------------------------------------------------
# Signup (citizen self-registration)
# ---------------------------------------------------------------------------

@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def signup():
    if current_user():
        return redirect(url_for("citizen.home"))

    countries = Country.query.filter_by(status="active").order_by(Country.name).all()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        country_id = request.form.get("country_id") or ""
        preferred_language = (request.form.get("preferred_language") or "en").strip()
        phone = (request.form.get("phone") or "").strip() or None
        consent = request.form.get("consent") == "on"

        errors = []
        if not name:
            errors.append("Please enter your name.")
        if not is_valid_email(email):
            errors.append("Please enter a valid email address.")
        if not any(c.id == country_id for c in countries):
            errors.append("Please select your country.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if not consent:
            errors.append("Please accept the privacy notice to create an account.")
        errors.extend(validate_password_strength(password))

        if not errors and _email_in_use(email):
            errors.append("An account with this email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "auth/signup.html", countries=countries,
                form_name=name, form_email=email, form_country_id=country_id,
            )

        now = datetime.now(timezone.utc)
        account = CitizenAccount(
            email=email,
            password_hash=hash_password(password),
            full_name=name,
            phone=phone,
            country_id=country_id,
            preferred_language=preferred_language or "en",
            consent_given_at=now,
            consent_version=CONSENT_VERSION,
            is_active=True,
            is_demo=False,
        )
        db.session.add(account)
        db.session.flush()
        log_action("citizen", account.id, "signup", "citizen_account", account.id,
                   ip_address=_client_ip())
        db.session.commit()

        login_user(account, "citizen")
        flash(f"Welcome, {account.full_name} — your account has been created.", "success")
        return redirect(url_for("citizen.home"))

    return render_template("auth/signup.html", countries=countries)


# ---------------------------------------------------------------------------
# Sign in (real accounts — citizen or government)
# ---------------------------------------------------------------------------

@auth_bp.route("/signin", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def signin():
    if current_user():
        return redirect(url_for("citizen.home"))

    next_url = request.values.get("next") or ""

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        now = datetime.now(timezone.utc)

        account = CitizenAccount.query.filter_by(email=email).first()
        account_type = "citizen"
        if account is None:
            account = GovernmentAccount.query.filter_by(email=email).first()
            account_type = "government"

        def _fail(message):
            flash(message, "error")
            return render_template("auth/signin.html", next=next_url, form_email=email)

        if account is None or account.is_demo:
            # is_demo accounts never authenticate with a password — treat
            # as "no such account" rather than leaking which emails exist.
            log_action("anonymous", None, "login_failed", account_type, None, ip_address=_client_ip())
            db.session.commit()
            return _fail("Incorrect email or password.")

        if getattr(account, "locked_until", None) and account.locked_until > now:
            return _fail("Too many failed attempts. Try again in a few minutes, or use 'Forgot password'.")

        if not account.is_active:
            return _fail("This account is not active. Contact your administrator.")

        if not verify_password(password, account.password_hash):
            account.failed_login_attempts = getattr(account, "failed_login_attempts", 0) + 1
            if account.failed_login_attempts >= MAX_FAILED_LOGINS:
                account.locked_until = now + LOCKOUT_DURATION
            log_action(account_type, account.id, "login_failed", account_type, account.id,
                       ip_address=_client_ip())
            db.session.commit()
            return _fail("Incorrect email or password.")

        login_user(account, account_type)
        log_action(account_type, account.id, "login", account_type, account.id,
                   ip_address=_client_ip())
        if hasattr(account, "failed_login_attempts"):
            account.failed_login_attempts = 0
            account.locked_until = None
        db.session.commit()

        flash(f"Welcome back, {account.full_name or account.email}.", "success")

        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        if account_type == "government":
            return redirect(url_for("government.dashboard"))
        return redirect(url_for("citizen.home"))

    return render_template("auth/signin.html", next=next_url)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@auth_bp.route("/logout", methods=["POST"])
def logout():
    account = current_user()
    account_type = current_account_type()
    if account is not None:
        log_action(account_type, account.id, "logout", account_type, account.id, ip_address=_client_ip())
        db.session.commit()
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("role_select"))


# ---------------------------------------------------------------------------
# Account / profile
# ---------------------------------------------------------------------------

@auth_bp.route("/account", methods=["GET", "POST"])
@require_login
def account():
    from app.models.auth_models import AccountSession

    acc = current_user()
    account_type = current_account_type()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "change_password":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""

            if acc.is_demo:
                flash("Demo accounts cannot change their password.", "error")
            elif not verify_password(current_password, acc.password_hash):
                flash("Current password is incorrect.", "error")
            elif new_password != confirm:
                flash("New passwords do not match.", "error")
            else:
                errors = validate_password_strength(new_password)
                if errors:
                    for e in errors:
                        flash(e, "error")
                else:
                    acc.password_hash = hash_password(new_password)
                    log_action(account_type, acc.id, "password_changed", account_type, acc.id,
                               ip_address=_client_ip())
                    db.session.commit()
                    flash("Password updated.", "success")

        elif action == "logout_other_sessions":
            current_raw = session.get("sid")
            current_hash = hash_token(current_raw) if current_raw else None
            others = (
                AccountSession.query
                .filter(
                    AccountSession.account_type == account_type,
                    AccountSession.account_id == acc.id,
                    AccountSession.revoked_at.is_(None),
                )
                .all()
            )
            revoked = 0
            for s in others:
                if s.token_hash != current_hash:
                    s.revoked_at = datetime.now(timezone.utc)
                    revoked += 1
            db.session.commit()
            flash(f"Signed out of {revoked} other session(s).", "success")

        return redirect(url_for("auth.account"))

    active_sessions = (
        AccountSession.query
        .filter(
            AccountSession.account_type == account_type,
            AccountSession.account_id == acc.id,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > datetime.now(timezone.utc),
        )
        .order_by(AccountSession.last_seen_at.desc())
        .all()
    )

    return render_template(
        "auth/account.html", user=acc, account_type=account_type,
        active_sessions=active_sessions,
    )


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        account = CitizenAccount.query.filter_by(email=email, is_demo=False).first()
        account_type = "citizen"
        if account is None:
            account = GovernmentAccount.query.filter_by(email=email, is_demo=False).first()
            account_type = "government"

        if account is not None:
            raw_token = generate_raw_token()
            db.session.add(PasswordResetToken(
                account_type=account_type,
                account_id=account.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(timezone.utc) + RESET_TOKEN_LIFETIME,
            ))
            log_action(account_type, account.id, "password_reset_requested", account_type, account.id,
                       ip_address=_client_ip())
            db.session.commit()

            reset_url = url_for("auth.reset_password", token=raw_token, _external=True)
            # Queued (not sent inline) so a slow/unreachable SMTP server
            # never blocks this request — see app/services/job_queue.py.
            # queue_email() itself returns False immediately (no job, no
            # network call) when SMTP isn't configured at all.
            queue_email(
                account.email,
                "Reset your BRICS People First password",
                f"Hello {account.full_name or account.email},\n\n"
                f"Use this link to reset your password (valid 1 hour):\n{reset_url}\n\n"
                "If you didn't request this, you can ignore this email.",
            )
            db.session.commit()  # persist the queued job row (if any)

        # Same message whether or not the account exists — prevents using
        # this form to discover which emails have accounts.
        flash("If an account exists for that email, a reset link has been sent.", "info")
        return redirect(url_for("auth.signin"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    from app.models.auth_models import AccountSession

    reset = PasswordResetToken.query.filter_by(token_hash=hash_token(token)).first()
    now = datetime.now(timezone.utc)
    valid = reset is not None and reset.used_at is None and reset.expires_at > now

    if not valid:
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if new_password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)

        errors = validate_password_strength(new_password)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/reset_password.html", token=token)

        model = CitizenAccount if reset.account_type == "citizen" else GovernmentAccount
        account = db.session.get(model, reset.account_id)
        account.password_hash = hash_password(new_password)
        if hasattr(account, "failed_login_attempts"):
            account.failed_login_attempts = 0
            account.locked_until = None
        reset.used_at = now

        # Force re-authentication everywhere — a password reset should
        # invalidate any session that might exist from a compromised
        # credential, not just the browser doing the reset.
        AccountSession.query.filter_by(
            account_type=reset.account_type, account_id=reset.account_id, revoked_at=None
        ).update({"revoked_at": now})

        log_action(reset.account_type, account.id, "password_reset_completed",
                   reset.account_type, account.id, ip_address=_client_ip())
        db.session.commit()

        flash("Password updated — please sign in.", "success")
        return redirect(url_for("auth.signin"))

    return render_template("auth/reset_password.html", token=token)
