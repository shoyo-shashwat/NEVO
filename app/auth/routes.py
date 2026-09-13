# auth/routes.py
#
# Real authentication — citizen signup/login/logout/profile/password reset,
# government login, and admin-provisioned government account creation.
# No public signup route exists for government roles (Phase 0 design §3).

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user

from app.extensions import db, limiter
from app.auth.forms import (
    CitizenSignupForm, CitizenLoginForm, GovernmentLoginForm,
    PasswordResetRequestForm, PasswordResetConfirmForm,
    ProvisionGovernmentAccountForm,
)
from app.auth.security import (
    hash_password, verify_password, generate_reset_token, hash_reset_token,
    CURRENT_CONSENT_VERSION,
)
from app.auth.rbac import current_citizen_account, current_government_account, require_government_role
from app.models.accounts import CitizenAccount, GovernmentAccount, PasswordResetToken, Department
from app.models.shared import Country, AdministrativeRegion
from app.services.audit import log_action
from app.services.email_client import send_email, EmailNotConfiguredError

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, template_folder="templates", url_prefix="/auth")

_RESET_TOKEN_TTL_MINUTES = 30


@auth_bp.route("/login")
def choose_login():
    """Small chooser page — 'I'm a citizen' vs 'I'm a government official'."""
    if current_citizen_account():
        return redirect(url_for("citizen.home"))
    if current_government_account():
        return redirect(url_for("government.dashboard"))
    return render_template("auth/choose_login.html")


# ---------------------------------------------------------------------------
# Citizen — signup, login, logout, profile
# ---------------------------------------------------------------------------

@auth_bp.route("/citizen/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def citizen_signup():
    if current_citizen_account():
        return redirect(url_for("citizen.home"))

    form = CitizenSignupForm()
    if form.validate_on_submit():
        existing = CitizenAccount.query.filter_by(email=form.email.data.lower().strip()).first()
        if existing:
            flash("An account with this email already exists. Try signing in instead.", "error")
            return render_template("auth/citizen_signup.html", form=form)

        country = Country.query.filter_by(code="IN").first()
        account = CitizenAccount(
            email=form.email.data.lower().strip(),
            password_hash=hash_password(form.password.data),
            full_name=form.full_name.data or None,
            phone=form.phone.data or None,
            country_id=country.id if country else None,
            consent_given_at=datetime.now(timezone.utc),
            consent_version=CURRENT_CONSENT_VERSION,
        )
        db.session.add(account)
        db.session.flush()
        log_action("citizen", account.id, "signup", "CitizenAccount", account.id)
        db.session.commit()

        login_user(account)
        flash("Welcome to NEVO — your account has been created.", "success")
        return redirect(url_for("citizen.home"))

    return render_template("auth/citizen_signup.html", form=form)


@auth_bp.route("/citizen/login", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def citizen_login():
    if current_citizen_account():
        return redirect(url_for("citizen.home"))

    form = CitizenLoginForm()
    if form.validate_on_submit():
        account = CitizenAccount.query.filter_by(email=form.email.data.lower().strip()).first()
        if not account or not verify_password(account.password_hash, form.password.data):
            log_action("anonymous", None, "login_failed", "CitizenAccount", None,
                       after_state={"email": form.email.data.lower().strip()})
            db.session.commit()
            flash("Incorrect email or password.", "error")
            return render_template("auth/citizen_login.html", form=form)
        if not account.is_active:
            flash("This account has been deactivated. Contact support if this is unexpected.", "error")
            return render_template("auth/citizen_login.html", form=form)

        account.last_login_at = datetime.now(timezone.utc)
        log_action("citizen", account.id, "login", "CitizenAccount", account.id)
        db.session.commit()
        login_user(account)
        return redirect(url_for("citizen.home"))

    return render_template("auth/citizen_login.html", form=form)


@auth_bp.route("/citizen/profile", methods=["GET", "POST"])
def citizen_profile():
    account = current_citizen_account()
    if not account:
        flash("Please sign in to view your profile.", "info")
        return redirect(url_for("auth.citizen_login"))

    if request.method == "POST":
        account.full_name = (request.form.get("full_name") or "").strip() or None
        account.phone = (request.form.get("phone") or "").strip() or None
        pref_lang = (request.form.get("preferred_language") or "en").strip()
        if pref_lang in ("en", "hi"):
            account.preferred_language = pref_lang
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.citizen_profile"))

    return render_template("auth/citizen_profile.html", account=account)


# ---------------------------------------------------------------------------
# Government — login only. No signup route exists (Phase 0 design §3).
# ---------------------------------------------------------------------------

@auth_bp.route("/government/login", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def government_login():
    if current_government_account():
        return redirect(url_for("government.dashboard"))

    form = GovernmentLoginForm()
    if form.validate_on_submit():
        account = GovernmentAccount.query.filter_by(email=form.email.data.lower().strip()).first()
        if not account or not verify_password(account.password_hash, form.password.data):
            log_action("anonymous", None, "login_failed", "GovernmentAccount", None,
                       after_state={"email": form.email.data.lower().strip()})
            db.session.commit()
            flash("Incorrect email or password.", "error")
            return render_template("auth/government_login.html", form=form)
        if not account.is_active:
            flash("This account has been deactivated.", "error")
            return render_template("auth/government_login.html", form=form)

        account.last_login_at = datetime.now(timezone.utc)
        log_action("government", account.id, "login", "GovernmentAccount", account.id)
        db.session.commit()
        login_user(account)
        return redirect(url_for("government.dashboard"))

    return render_template("auth/government_login.html", form=form)


@auth_bp.route("/government/provision", methods=["GET", "POST"])
@require_government_role("national_admin", "state_admin")
def provision_government_account():
    """
    Admin-provisioned government account creation (Phase 0 design §3). A
    state_admin may only provision accounts scoped to their own region; a
    national_admin may provision anyone anywhere.
    """
    actor = current_government_account()
    form = ProvisionGovernmentAccountForm()
    form.region_id.choices = [("", "— none (national) —")] + [
        (r.id, r.name) for r in AdministrativeRegion.query.filter_by(country_id=actor.country_id).order_by(AdministrativeRegion.name).all()
    ]
    form.department_id.choices = [("", "— none —")] + [
        (d.id, d.name) for d in Department.query.filter_by(country_id=actor.country_id).order_by(Department.name).all()
    ]

    if form.validate_on_submit():
        if actor.role == "state_admin" and form.region_id.data and form.region_id.data != actor.region_id:
            flash("You may only provision accounts within your own region.", "error")
            return render_template("auth/provision_government.html", form=form)

        existing = GovernmentAccount.query.filter_by(email=form.email.data.lower().strip()).first()
        if existing:
            flash("An account with this email already exists.", "error")
            return render_template("auth/provision_government.html", form=form)

        import secrets
        temp_password = secrets.token_urlsafe(12)
        new_account = GovernmentAccount(
            email=form.email.data.lower().strip(),
            password_hash=hash_password(temp_password),
            full_name=form.full_name.data.strip(),
            role=form.role.data,
            country_id=actor.country_id,
            region_id=form.region_id.data or None,
            department_id=form.department_id.data or None,
            provisioned_by=actor.id,
        )
        db.session.add(new_account)
        db.session.flush()
        log_action("government", actor.id, "provision_account", "GovernmentAccount", new_account.id,
                   after_state={"role": new_account.role, "email": new_account.email})
        db.session.commit()

        flash(
            f"Account created for {new_account.email}. Temporary password: {temp_password} "
            "— share this through a secure channel; it is shown only once and cannot be "
            "retrieved again.",
            "success",
        )
        return redirect(url_for("government.admin"))

    return render_template("auth/provision_government.html", form=form)


# ---------------------------------------------------------------------------
# Shared — logout, password reset
# ---------------------------------------------------------------------------

@auth_bp.route("/logout", methods=["POST"])
def logout():
    was_gov = current_government_account() is not None
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("government.dashboard") if was_gov else url_for("citizen.home"))


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def request_password_reset():
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        account = CitizenAccount.query.filter_by(email=email).first()
        account_type = "citizen"
        if account is None:
            account = GovernmentAccount.query.filter_by(email=email).first()
            account_type = "government"

        # Always show the same message whether or not the email exists —
        # do not leak account existence through response differences.
        if account:
            raw_token, token_hash = generate_reset_token()
            db.session.add(PasswordResetToken(
                account_type=account_type,
                account_id=account.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_TTL_MINUTES),
            ))
            db.session.commit()
            reset_url = url_for("auth.confirm_password_reset", token=raw_token, _external=True)
            try:
                send_email(
                    email, "Reset your NEVO password",
                    f"Use this link within {_RESET_TOKEN_TTL_MINUTES} minutes to reset your password:\n{reset_url}",
                )
            except EmailNotConfiguredError:
                flash(
                    "Password reset emails are not yet configured on this server "
                    "(no SMTP credentials set). Contact an administrator to reset "
                    "your password manually.",
                    "error",
                )
                return render_template("auth/reset_request.html", form=form)

        flash("If that email exists, a reset link has been sent.", "info")
        return redirect(url_for("auth.request_password_reset"))

    return render_template("auth/reset_request.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def confirm_password_reset(token):
    token_hash = hash_reset_token(token)
    reset_row = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if not reset_row or reset_row.used_at or reset_row.expires_at < datetime.now(timezone.utc):
        flash("This reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.request_password_reset"))

    form = PasswordResetConfirmForm(token=token)
    if form.validate_on_submit():
        model = CitizenAccount if reset_row.account_type == "citizen" else GovernmentAccount
        account = db.session.get(model, reset_row.account_id)
        if not account:
            flash("Account no longer exists.", "error")
            return redirect(url_for("auth.request_password_reset"))

        account.password_hash = hash_password(form.password.data)
        reset_row.used_at = datetime.now(timezone.utc)
        log_action(reset_row.account_type, account.id, "password_reset", type(account).__name__, account.id)
        db.session.commit()
        flash("Your password has been reset. You can now sign in.", "success")
        return redirect(
            url_for("auth.citizen_login") if reset_row.account_type == "citizen"
            else url_for("auth.government_login")
        )

    return render_template("auth/reset_confirm.html", form=form)
