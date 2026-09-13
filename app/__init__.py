# app/__init__.py
# Application factory — wiring only, no business logic.
#
# Phase 0 (docs/superpowers/specs/2026-09-12-phase0-foundations-design.md):
# real auth (Flask-Login), CSRF (Flask-WTF), rate limiting (Flask-Limiter)
# replace the old session-only demo-actor picker.
#
# db.init_app() and migrate.init_app() are called ONLY here.
# No other file in the codebase should call either of those.

from flask import Flask, render_template, g

from app.config import Config
from app.extensions import db, migrate, login_manager, csrf, limiter


def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(config_class)

    # ------------------------------------------------------------------
    # 1. Extensions
    # ------------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.choose_login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "info"
    csrf.init_app(app)
    limiter.init_app(app)

    @login_manager.user_loader
    def load_user(composite_id: str):
        from app.models.accounts import CitizenAccount, GovernmentAccount
        kind, _, raw_id = composite_id.partition(":")
        if kind == "citizen":
            return db.session.get(CitizenAccount, raw_id)
        if kind == "gov":
            return db.session.get(GovernmentAccount, raw_id)
        return None

    # ------------------------------------------------------------------
    # 2. Model discovery — must run inside app context so Flask-Migrate
    #    sees all tables.
    # ------------------------------------------------------------------
    with app.app_context():
        from app import models as _models  # noqa: F401

    # ------------------------------------------------------------------
    # 3. Blueprint registration — auth, citizen, government.
    #    citizen/ and government/ never import from each other (verified
    #    by grep). auth/ is shared infrastructure both depend on.
    # ------------------------------------------------------------------
    from app.auth.routes import auth_bp
    from app.citizen import citizen_bp
    from app.government import government_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(citizen_bp)
    app.register_blueprint(government_bp)

    # ------------------------------------------------------------------
    # 4. Root route — the public NEVO landing page (frontpage.png design).
    #    Real auth replaced the old demo role-selector; anonymous visitors
    #    and signed-in citizens both land on citizen.home, government users
    #    are sent to their dashboard.
    # ------------------------------------------------------------------
    @app.route("/")
    def root():
        from flask import redirect, url_for
        from app.auth.rbac import current_government_account
        if current_government_account():
            return redirect(url_for("government.dashboard"))
        return redirect(url_for("citizen.home"))

    # ------------------------------------------------------------------
    # 5. Anonymous-identity cookie — a citizen who submits without an
    #    account gets a persistent, unguessable token (app/auth/security.py)
    #    so distinct anonymous contributors count as distinct. The token is
    #    minted lazily on first use and queued onto `g`; this hook is what
    #    actually attaches it to the outgoing response.
    # ------------------------------------------------------------------
    @app.after_request
    def _apply_pending_anonymous_cookie(response):
        set_cookie_fn = getattr(g, "_pending_anon_cookie", None)
        if set_cookie_fn:
            response = set_cookie_fn(response)
        return response

    # ------------------------------------------------------------------
    # 6. CLI — one-time bootstrap for the very first national_admin.
    #    Every subsequent government account is created through
    #    auth.provision_government_account by an existing admin.
    # ------------------------------------------------------------------
    from app.cli import register_cli
    register_cli(app)

    # ------------------------------------------------------------------
    # 7. Error handlers — friendly pages, no raw tracebacks on-screen
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error("Internal server error: %s", e)
        return render_template("errors/500.html"), 500

    return app
