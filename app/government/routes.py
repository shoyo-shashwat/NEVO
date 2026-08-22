# government/routes.py
#
# All government-facing routes — Screens 1–6 per §21.2.
# One blueprint shared by MP and Planning Officer, role-gated internally.
#
# Hard rules:
#   - Never import from citizen/ — citizen data read directly from models/.
#   - Scoring logic stays in services/ — never inlined here.
#   - GovernmentDecision.reason is HUMAN-AUTHORED ONLY.
#     No AI-generated text may populate this field under any circumstance.
#     The form must source reason exclusively from request.form.

from flask import render_template, request, session, redirect, url_for, flash

from app.government import government_bp
from app.extensions import db
from app.auth.session import require_role, current_actor_id, current_role

# Models — citizen-originated data read directly from models/, no citizen/ import
from app.models.demand_cluster import DemandCluster
from app.models.citizen_models import Contribution, Verification  # read-only counts
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.models.reference_data import InfrastructureDataPoint, GovernmentInvestment
from app.models.shared import Category, Country, AdministrativeRegion

# Services — scoring lives here only, never inlined in routes
from app.services import gap_assessment, investment_alignment, priority_scoring


# ---------------------------------------------------------------------------
# Screen 1 — Dashboard
# ---------------------------------------------------------------------------

@government_bp.route("/dashboard")
@require_role("mp", "planning_officer")
def dashboard():
    """
    Top priorities + emerging gaps + pending decisions.
    One list of priority cards — no charts, no filter panels.
    """
    country_code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=country_code).first()
    country_id = country.id if country else None

    # Active clusters for this country, ordered by trend then recency
    clusters = (
        DemandCluster.query
        .filter(
            DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
            DemandCluster.country_id == country_id,
        )
        .order_by(DemandCluster.updated_at.desc())
        .limit(20)
        .all()
    )

    # Compute a lightweight priority signal for each card.
    # dominant_severity is passed to stage2_priority AND into the template
    # so the displayed severity and the scored severity are always the same value.
    cards = []
    for c in clusters:
        cat = db.session.get(Category, c.category_id)
        sev = _dominant_severity(c.id)
        # "Pending decision" = cluster is actively in the government workflow
        # but no GovernmentDecision has been recorded yet.
        # review_status "NotReviewed" is NOT pending — it hasn't been opened.
        # review_status "UnderReview" or "PendingValidation" means a government
        # actor has opened it and it awaits a formal decision (Progress Log §13.3).
        pending = c.review_status in ("UnderReview", "PendingValidation")
        cards.append({
            "cluster": c,
            "category_name": cat.name if cat else "",
            "total_reports": c.total_reports,
            "unique_contributors": c.unique_contributors,
            "sentiment": c.community_sentiment,
            "dominant_severity": sev,
            "pending_decision": pending,
        })

    # Split into sections per Government §5 (no extra sections beyond spec)
    top = [card for card in cards if card["cluster"].trend == "increasing"]
    pending = [card for card in cards if card["pending_decision"]]
    rest = [card for card in cards if card not in top and card not in pending]

    return render_template(
        "government/dashboard.html",
        top_cards=top[:5],
        pending_cards=pending[:5],
        other_cards=rest[:10],
        country_code=country_code,
    )


# ---------------------------------------------------------------------------
# Screen 2 — Demand Map
# ---------------------------------------------------------------------------

@government_bp.route("/map")
@require_role("mp", "planning_officer")
def demand_map():
    """Government demand intelligence map — same GeoJSON source as citizen map."""
    categories = Category.query.all()
    return render_template("government/demand_map.html", categories=categories)


# ---------------------------------------------------------------------------
# Screen 3 — Priority / Evidence Detail
# ---------------------------------------------------------------------------

@government_bp.route("/demand/<cluster_id>")
@require_role("mp", "planning_officer")
def evidence_detail(cluster_id):
    """
    Single Priority Evidence Card.
    All scoring computed fresh per request — no caching, no background jobs.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    cat = db.session.get(Category, cluster.category_id)

    # Compute fresh — three service calls, pure functions, no side effects.
    # dominant_severity is computed once and passed to both stage2_priority
    # and the template — same value scored and displayed, no silent disagreement.
    gap = gap_assessment.calculate(cluster_id)
    alignment = investment_alignment.calculate(cluster_id, gap_assessment=gap)
    dominant_sev = _dominant_severity(cluster_id)
    s1 = priority_scoring.stage1_confidence(gap, alignment)
    s2 = priority_scoring.stage2_priority(
        confidence=s1.confidence,
        severity=dominant_sev,
        population_affected=gap.population_affected,
        gap_confidence=gap.confidence,
        trend=cluster.trend,
        alignment_state=alignment.state,
    )

    # Existing decision if any
    existing_decision = (
        GovernmentDecision.query
        .filter_by(demand_cluster_id=cluster_id)
        .order_by(GovernmentDecision.timestamp.desc())
        .first()
    )

    return render_template(
        "government/evidence_detail.html",
        cluster=cluster,
        category=cat,
        gap=gap,
        alignment=alignment,
        confidence=s1,
        priority=s2,
        dominant_severity=dominant_sev,   # same value passed to stage2_priority
        existing_decision=existing_decision,
        role=current_role(),
    )


# ---------------------------------------------------------------------------
# Screen 4 — Decision Workspace
# ---------------------------------------------------------------------------

@government_bp.route("/demand/<cluster_id>/decide", methods=["GET", "POST"])
@require_role("mp", "planning_officer")
def decision_workspace(cluster_id):
    """
    Human decision workspace.

    GET  — show decision form with contextual action set based on role +
           current workflow state.
    POST — record GovernmentDecision.

    HARD INVARIANT: reason must come from request.form only.
    No AI-generated text may populate GovernmentDecision.reason.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    role = current_role()

    existing_decision = (
        GovernmentDecision.query
        .filter_by(demand_cluster_id=cluster_id)
        .order_by(GovernmentDecision.timestamp.desc())
        .first()
    )

    if request.method == "POST":
        decision_type = request.form.get("decision_type")
        # Reason sourced exclusively from the human-submitted form field.
        # AI must never be called to generate or populate this value.
        reason = (request.form.get("reason") or "").strip()
        linked_project_id = request.form.get("linked_project_id") or None

        if not reason:
            flash("A reason is required for every decision.", "error")
            return redirect(url_for("government.decision_workspace",
                                    cluster_id=cluster_id))

        valid_types_mp = ("Prioritize", "Defer", "Deprioritize", "NeedsValidation",
                          "Redirected")
        valid_types_po = ("NeedsValidation", "Redirected", "Prioritize")
        valid = valid_types_mp if role == "mp" else valid_types_po
        if decision_type not in valid:
            flash("Invalid decision type for your role.", "error")
            return redirect(url_for("government.decision_workspace",
                                    cluster_id=cluster_id))

        decision = GovernmentDecision(
            demand_cluster_id=cluster_id,
            country_id=cluster.country_id,
            decided_by_id=current_actor_id(),
            decided_by_role=role,
            decision_type=decision_type,
            reason=reason,          # human-authored — sourced from form only
            linked_project_id=linked_project_id,
        )
        db.session.add(decision)

        # Update cluster workflow state
        cluster.review_status = "Decided"
        if decision_type == "Prioritize":
            cluster.active_status = "UnderGovernmentReview"
        elif decision_type == "Defer":
            cluster.active_status = "Deferred"
        elif decision_type == "Deprioritize":
            cluster.active_status = "Deprioritized"

        db.session.commit()
        flash("Decision recorded.", "success")
        return redirect(url_for("government.evidence_detail",
                                cluster_id=cluster_id))

    # GET — collect available projects for the "Link project" dropdown
    projects = Project.query.filter_by(country_id=cluster.country_id).all()

    return render_template(
        "government/decision_workspace.html",
        cluster=cluster,
        role=role,
        existing_decision=existing_decision,
        projects=projects,
    )


# ---------------------------------------------------------------------------
# Screen 5 — Projects / Outcomes
# ---------------------------------------------------------------------------

@government_bp.route("/projects")
@require_role("mp", "planning_officer")
def projects_outcomes():
    """Linked interventions and progress/outcome status."""
    country_code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=country_code).first()
    country_id = country.id if country else None

    projects = (
        Project.query
        .filter_by(country_id=country_id)
        .order_by(Project.updated_at.desc())
        .all()
    )

    project_data = []
    for p in projects:
        outcome = Outcome.query.filter_by(project_id=p.id).first()
        cluster = (
            db.session.get(DemandCluster, p.linked_demand_cluster_id)
            if p.linked_demand_cluster_id else None
        )
        project_data.append({
            "project": p,
            "outcome": outcome,
            "cluster": cluster,
        })

    return render_template(
        "government/projects_outcomes.html",
        project_data=project_data,
    )


# ---------------------------------------------------------------------------
# Screen 6 — Admin
# ---------------------------------------------------------------------------

@government_bp.route("/admin", methods=["GET", "POST"])
@require_role("admin")
def admin():
    """
    Technical configuration only — no policy authority (Progress Log §5.1).
    MVP scope: view seeded countries, categories, actor list.
    """
    countries = Country.query.all()
    categories = Category.query.all()

    from app.auth.session import get_all_demo_actors
    actors = get_all_demo_actors()

    return render_template(
        "government/admin.html",
        countries=countries,
        categories=categories,
        actors=actors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dominant_severity(cluster_id: str):
    """
    Return the most common non-null severity across a cluster's reports (mode).
    This value is passed to stage2_priority() AND rendered in evidence_detail.html
    so the scored severity and the displayed severity are always identical.
    Never call this separately from the template render — always pass the result
    through as a template variable.
    """
    from app.models.citizen_models import Report
    from sqlalchemy import func

    row = (
        db.session.query(Report.severity, func.count(Report.id).label("cnt"))
        .join(Contribution, Contribution.report_id == Report.id)
        .filter(Contribution.demand_cluster_id == cluster_id)
        .filter(Report.severity.isnot(None))
        .group_by(Report.severity)
        .order_by(func.count(Report.id).desc())
        .first()
    )
    return row.severity if row else None
