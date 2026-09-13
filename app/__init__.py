# app/__init__.py
# Application factory — wiring only, no business logic.
#
# Build order (Master Prompt §5):
#   Steps 1–7 complete. Step 8: wiring. Step 9: deploy.
#
# db.init_app() and migrate.init_app() are called ONLY here.
# No other file in the codebase should call either of those.

from flask import Flask, render_template, request, redirect, url_for, session, flash

from app.config import Config
from app.extensions import db, migrate, csrf, limiter


def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(config_class)

    # Session cookie hardening — HttpOnly is Flask's default; the rest are
    # not. A short-ish permanent lifetime plus DB-backed session revocation
    # (see app/auth/session.py) means a stolen cookie stops working the
    # moment the session row is revoked, without waiting for cookie expiry.
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_SECURE", not app.debug)
    from datetime import timedelta
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(days=30))

    # ------------------------------------------------------------------
    # 1. Extensions — db first, then migrate, then security extensions
    # ------------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    # ------------------------------------------------------------------
    # 2. Model discovery — must run inside app context so Flask-Migrate
    #    sees all tables.  Uses aliased import to avoid rebinding 'app'.
    # ------------------------------------------------------------------
    with app.app_context():
        from app import models as _models  # noqa: F401

    # ------------------------------------------------------------------
    # 3. Blueprint registration — citizen at /citizen, government at /gov,
    #    auth at the bare root (/signup, /signin, /logout, /account, ...).
    #    Coupling check: neither citizen/ nor government/ imports from the
    #    other. Verified by grep before this file was written (Step 8 audit).
    # ------------------------------------------------------------------
    from app.citizen import citizen_bp
    from app.government import government_bp
    from app.auth import auth_bp

    app.register_blueprint(citizen_bp)    # url_prefix="/citizen" set in blueprint
    app.register_blueprint(government_bp)  # url_prefix="/gov" set in blueprint
    app.register_blueprint(auth_bp)

    # ------------------------------------------------------------------
    # 3b. Load the current user (if any) on every request from the
    #     DB-backed session record — see app/auth/session.py.
    # ------------------------------------------------------------------
    from app.auth.session import load_logged_in_user
    app.before_request(load_logged_in_user)

    # ------------------------------------------------------------------
    # 4. Landing page ("/") + dedicated login page ("/login").
    #
    #    Round 4 UI directive: the marketing/explainer landing page and the
    #    demo-account picker used to live on the same template, which made
    #    the front door feel like "a demo" rather than a real product and
    #    buried the actual CTAs under an account switcher. They are now two
    #    separate routes/templates:
    #      "/"       role_select()  — pure landing page, no accounts shown.
    #      "/login"  login_page()   — the demo-account picker (grouped by
    #                                 country), POSTs back to itself.
    #    Both live here rather than in a blueprint because they sit above
    #    citizen/government and are shared infrastructure, not domain logic.
    #    The endpoint name "role_select" is kept (rather than renamed) so
    #    every existing url_for("role_select") reference — nav logo, error
    #    pages — keeps pointing at the landing page without a mass rename.
    # ------------------------------------------------------------------
    @app.route("/", methods=["GET"])
    def role_select():
        return render_template("role_select.html")

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        # One-click login for seeded is_demo=True accounts only — see
        # app/auth/session.py::set_demo_session(). Real citizen/government
        # accounts sign in at /signin or via an accepted invite; this path
        # can never authenticate them (set_demo_session rejects is_demo=False
        # rows even if someone guesses/forges an id).
        from app.auth.session import get_all_demo_actors, set_demo_session

        if request.method == "POST":
            actor_id = request.form.get("actor_id", "").strip()
            if not set_demo_session(actor_id):
                flash("Unknown actor — please select one from the list.", "error")
                return redirect(url_for("login_page"))

            role = session.get("role")
            if role in ("mp", "planning_officer", "admin"):
                return redirect(url_for("government.dashboard"))
            return redirect(url_for("citizen.home"))

        actors = get_all_demo_actors()
        return render_template("login.html", actors=actors)

    # ------------------------------------------------------------------
    # 4b. Every dynamic (non-static) response is session-dependent — the
    #     nav bar alone changes based on who's logged in — so none of it
    #     may be cached by the browser. Without this, a browser can (and,
    #     per a support session on 2026-09-13, did) keep serving a stale
    #     cached page/redirect indefinitely, showing an entirely different
    #     build than what the server currently returns. Static assets
    #     (CSS/JS/images) are unaffected — Flask's static handler sets its
    #     own cache headers separately.
    # ------------------------------------------------------------------
    @app.after_request
    def _no_cache_dynamic_responses(response):
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    # ------------------------------------------------------------------
    # 5. Error handlers — friendly pages, no raw tracebacks on-screen
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error("Internal server error: %s", e)
        return render_template("errors/500.html"), 500

    return app
