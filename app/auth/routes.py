# auth/routes.py
#
# Real account routes: citizen self-signup, password sign-in, logout,
# account/profile, and password reset — across the two real account tables
# (CitizenAccount, GovernmentAccount) described in app/models/auth_models.py.
# This is the ONLY way into the app — the old passwordless "/login" demo
# picker (set_demo_session/get_all_demo_actors) has been removed; seeded
# is_demo=True accounts now sign in the same way as everyone else, with
# their real (seeded) password — see seed/seed_data.py::DEMO_ACTOR_PASSWORD.
#
# Government accounts are never self-registered here — they're created
# directly by an admin from /gov/admin (see government/routes.py::
# provision_government_user), matching the existing schema's
# provisioned_by column and the absence of any invite-token table.

from datetime import datetime, timezone, timedelta

from flask import (
    render_template, request, redirect, url_for, flash, session
)

from app.auth import auth_bp
from app.extensions import db, limiter
from app.models.auth_models import (
    CitizenAccount, GovernmentAccount, PasswordResetToken, log_action,
)
from app.models.shared import Country, AdministrativeRegion
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


def _redirect_to_own_home():
    """
    Where an ALREADY-logged-in visitor to /signup or /signin belongs —
    never unconditionally citizen.home. A government account hitting either
    page (e.g. a stale bookmark, or clicking "Sign up" while still signed in
    as an MP) used to be redirected into the citizen app entirely, which
    left the government nav bar (driven by session['role']) showing on top
    of citizen page content — a real mixed-role bug caught during
    multi-state browser verification, not cosmetic.
    """
    if current_account_type() == "government":
        return redirect(url_for("government.dashboard"))
    return redirect(url_for("citizen.home"))


# ---------------------------------------------------------------------------
# Signup (citizen self-registration)
# ---------------------------------------------------------------------------

def _regions_payload(countries) -> dict:
    """
    {country_id: {"states": [{id,name}], "districts": {state_id: [{id,name}]}}}
    — everything the signup form's state/district cascading selects need,
    shaped so the client can filter without a round trip. Only state_province
    and district_municipality levels are surfaced here; constituency-level
    regions are government-scope-only (see app/auth/actors.py) and never a
    citizen signup choice.
    """
    country_ids = [c.id for c in countries]
    regions = (
        AdministrativeRegion.query
        .filter(
            AdministrativeRegion.country_id.in_(country_ids),
            AdministrativeRegion.level.in_(["state_province", "district_municipality"]),
        )
        .order_by(AdministrativeRegion.name)
        .all()
    )
    payload = {cid: {"states": [], "districts": {}} for cid in country_ids}
    for r in regions:
        bucket = payload.get(r.country_id)
        if bucket is None:
            continue
        if r.level == "state_province":
            bucket["states"].append({"id": r.id, "name": r.name})
        else:
            bucket["districts"].setdefault(r.parent_region_id, []).append({"id": r.id, "name": r.name})
    return payload


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def signup():
    if current_user():
        return _redirect_to_own_home()

    countries = Country.query.filter_by(status="active").order_by(Country.name).all()
    regions_payload = _regions_payload(countries)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        country_id = request.form.get("country_id") or ""
        state_region_id = request.form.get("state_region_id") or ""
        district_region_id = request.form.get("district_region_id") or ""
        locality = (request.form.get("locality") or "").strip() or None
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
        if not state_region_id:
            errors.append("Please select your state.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if not consent:
            errors.append("Please accept the privacy notice to create an account.")
        errors.extend(validate_password_strength(password))

        if not errors and _email_in_use(email):
            errors.append("An account with this email already exists.")

        # region_id stores the most specific administrative scope the
        # citizen gave us — the district if they picked one, else the state.
        # Validated against the actual seeded hierarchy so a tampered/stale
        # form value can never assign a region from a different country.
        region_id = None
        if not errors:
            candidate_id = district_region_id or state_region_id
            candidate = db.session.get(AdministrativeRegion, candidate_id) if candidate_id else None
            if candidate is None or candidate.country_id != country_id:
                errors.append("Please select a valid state/district.")
            else:
                region_id = candidate.id

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "auth/signup.html", countries=countries, regions_payload=regions_payload,
                form_name=name, form_email=email, form_country_id=country_id,
                form_state_region_id=state_region_id, form_district_region_id=district_region_id,
                form_locality=locality or "",
            )

        now = datetime.now(timezone.utc)
        account = CitizenAccount(
            email=email,
            password_hash=hash_password(password),
            full_name=name,
            phone=phone,
            country_id=country_id,
            region_id=region_id,
            locality=locality,
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

    return render_template("auth/signup.html", countries=countries, regions_payload=regions_payload)


# ---------------------------------------------------------------------------
# Sign in (real accounts — citizen or government)
# ---------------------------------------------------------------------------

@auth_bp.route("/signin", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def signin():
    if current_user():
        return _redirect_to_own_home()

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

        if account is None:
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

    # Personal contribution summary — same query pattern as
    # citizen/routes.py::home()/impact(), reused here so Profile can show
    # real "My impact" numbers instead of inventing new ones.
    personal = None
    if account_type == "citizen":
        from sqlalchemy import func
        from app.models.citizen_models import Report, Contribution
        from app.models.government_models import Outcome

        reports_count = Report.query.filter_by(citizen_account_id=acc.id).count()
        joined_count = Contribution.query.filter_by(citizen_account_id=acc.id).count()
        resolved_count = (
            db.session.query(func.count(func.distinct(Contribution.demand_cluster_id)))
            .join(Outcome, Outcome.demand_cluster_id == Contribution.demand_cluster_id)
            .filter(Contribution.citizen_account_id == acc.id, Outcome.status == "Verified")
            .scalar() or 0
        )
        personal = {
            "reports_submitted": reports_count,
            "demands_joined": joined_count,
            "issues_resolved": resolved_count,
        }

    return render_template(
        "auth/account.html", user=acc, account_type=account_type,
        active_sessions=active_sessions, personal=personal,
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
