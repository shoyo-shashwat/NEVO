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
from app.extensions import db, migrate


def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(config_class)

    # ------------------------------------------------------------------
    # 1. Extensions — db first, then migrate
    # ------------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)

    # ------------------------------------------------------------------
    # 2. Model discovery — must run inside app context so Flask-Migrate
    #    sees all tables.  Uses aliased import to avoid rebinding 'app'.
    # ------------------------------------------------------------------
    with app.app_context():
        from app import models as _models  # noqa: F401

    # ------------------------------------------------------------------
    # 3. Blueprint registration — citizen at /citizen, government at /gov.
    #    Coupling check: neither blueprint imports from the other.
    #    Verified by grep before this file was written (Step 8 audit).
    # ------------------------------------------------------------------
    from app.citizen import citizen_bp
    from app.government import government_bp

    app.register_blueprint(citizen_bp)    # url_prefix="/citizen" set in blueprint
    app.register_blueprint(government_bp)  # url_prefix="/gov" set in blueprint

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
        from app.auth.session import get_all_demo_actors, set_session

        if request.method == "POST":
            actor_id = request.form.get("actor_id", "").strip()
            if not set_session(actor_id):
                flash("Unknown actor — please select one from the list.", "error")
                return redirect(url_for("login_page"))

            role = session.get("role")
            if role in ("mp", "planning_officer", "admin"):
                return redirect(url_for("government.dashboard"))
            return redirect(url_for("citizen.home"))

        actors = get_all_demo_actors()
        return render_template("login.html", actors=actors)

    # ------------------------------------------------------------------
    # 5. load_demo_actors() called here — not only in seed_data.py.
    #    This ensures the in-memory actor registry (_DEMO_ACTORS dict in
    #    auth/session.py) is populated on every app boot, including on
    #    Render after a restart, without needing to re-run the seed script.
    #    Reads actor rows from the database; safe to call on every boot.
    # ------------------------------------------------------------------
    with app.app_context():
        _bootstrap_demo_actors(app)

    # ------------------------------------------------------------------
    # 6. Error handlers — friendly pages, no raw tracebacks on-screen
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error("Internal server error: %s", e)
        return render_template("errors/500.html"), 500

    return app


# ---------------------------------------------------------------------------
# Demo actor bootstrap — wiring only, no business logic
# ---------------------------------------------------------------------------

def _bootstrap_demo_actors(app):
    """
    Populate auth/session._DEMO_ACTORS from the single source of truth
    in app/auth/actors.py.  Called on every app boot inside create_app()
    so the role selector works immediately after startup on Render without
    re-running the seed script (Progress Log §3 open item — now closed).
    """
    from app.auth.actors import DEMO_ACTORS
    from app.auth.session import load_demo_actors
    load_demo_actors(DEMO_ACTORS)
