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

from flask import render_template, request, redirect, url_for, flash, abort

from app.government import government_bp
from app.extensions import db
from app.auth.session import current_actor_id, current_role
from app.auth.rbac import (
    current_government_account, require_government_role,
    require_implementation_authority, scoped_region_ids, scoped_department_ids,
)
from app.models.accounts import (
    DECISION_AUTHORITY_ROLES, IMPLEMENTATION_ROLES, EVIDENCE_ONLY_ROLES,
    GovernmentAccount, Department,
)

# Models — citizen-originated data read directly from models/, no citizen/ import
from app.models.demand_cluster import DemandCluster
from app.models.citizen_models import Contribution, Verification  # read-only counts
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.models.reference_data import InfrastructureDataPoint, GovernmentInvestment
from app.models.shared import Category, Country, AdministrativeRegion, EventLog

# Services — scoring lives here only, never inlined in routes
from app.services import gap_assessment, investment_alignment, priority_scoring
from app.services import report_lifecycle
from app.services.audit import log_action

_ALL_GOV_ROLES = (
    "national_admin", "state_admin", "district_officer",
    "department_officer", "analyst", "reviewer",
)


def _cluster_in_scope(cluster: DemandCluster, account: GovernmentAccount) -> bool:
    """
    Server-side scope check (Phase 0 design §2) — a cluster is in scope if
    the account is unscoped (national_admin / no region set) or if any of
    the cluster's region_ids fall within the account's scoped region tree.

    A cluster with no region_ids at all (location never resolved — e.g. the
    citizen's report had no matchable location) is treated as in-scope for
    any government account in the same country: hiding an unresolved
    cluster from every regional officer would mean nobody could ever review
    it or assign it a region, which is worse than showing it broadly.
    """
    if not cluster.region_ids:
        return True
    allowed_regions = scoped_region_ids(account)
    if allowed_regions is None:
        return True
    cluster_regions = set(cluster.region_ids)
    return bool(cluster_regions & set(allowed_regions))


# ---------------------------------------------------------------------------
# Screen 1 — Dashboard
# ---------------------------------------------------------------------------

_PRIORITY_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
_UNADDRESSED_STATES = ("UNADDRESSED", "PARTIALLY_ADDRESSED", "IMPLEMENTATION_ACCESS_GAP")


@government_bp.route("/dashboard")
@require_government_role(*_ALL_GOV_ROLES)
def dashboard():
    """
    Government §5 specifies four sections — Top Priorities, Emerging Gaps,
    Investment/Intervention Gaps, Decisions Awaiting Review — each answering
    a different question about the same pool of active clusters. Computes
    the real Stage 1/Stage 2 priority score and investment alignment for
    each cluster (the same services evidence_detail() uses) so "Top
    Priorities" is actually ranked by priority, not merely by trend, and so
    "Investment/Intervention Gaps" reflects real alignment state rather than
    being folded into the other two sections.

    Framing is role-dependent per the supplied designs: decision-authority
    roles (national/state admin, district officer) see "What needs your
    decision"; department_officer sees "What needs implementation follow-up"
    plus their own in-progress projects; analyst/reviewer get the same data
    read-only (no action buttons — enforced server-side in decision_workspace
    and the project routes, not just hidden in the template).
    """
    account = current_government_account()
    country_id = account.country_id

    allowed_region_ids = scoped_region_ids(account)
    allowed_department_ids = scoped_department_ids(account)

    # Active clusters for this country, ordered by trend then recency
    clusters = (
        DemandCluster.query
        .filter(
            DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
            DemandCluster.country_id == country_id,
        )
        .order_by(DemandCluster.updated_at.desc())
        .limit(50)
        .all()
    )
    if allowed_region_ids is not None:
        allowed_set = set(allowed_region_ids)
        clusters = [c for c in clusters if allowed_set & set(c.region_ids or [])]
    clusters = clusters[:20]

    cards = []
    for c in clusters:
        cat = db.session.get(Category, c.category_id)
        sev = _dominant_severity(c.id)

        # Same computed-fresh services evidence_detail() uses — Government §5.3.4:
        # cheap enough at dashboard scale (<=20 clusters), and this is what makes
        # "Top Priorities" and "Investment/Intervention Gaps" real signals rather
        # than trend-only approximations.
        gap = gap_assessment.calculate(c.id)
        alignment = investment_alignment.calculate(c.id, gap_assessment=gap)
        s1 = priority_scoring.stage1_confidence(gap, alignment)
        s2 = priority_scoring.stage2_priority(
            confidence=s1.confidence,
            severity=sev,
            population_affected=gap.population_affected,
            gap_confidence=gap.confidence,
            trend=c.trend,
            alignment_state=alignment.state,
        )

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
            "priority": s2.priority,
            "alignment_state": alignment.state,
        })

    # Government §5 — four independent lenses over the same pool. A cluster can
    # legitimately answer more than one question at once (e.g. both the highest
    # priority AND awaiting a decision) so sections are not mutually exclusive,
    # same as the doc's own four bullet groups aren't framed as exclusive.
    top = sorted(
        [card for card in cards if card["priority"] in ("CRITICAL", "HIGH")],
        key=lambda card: _PRIORITY_RANK[card["priority"]],
        reverse=True,
    )
    # "Emerging Gaps" per doc §5 is two signals, not one: rapidly increasing
    # demand (trend) AND growing service deficits (the EMERGING_GAP alignment
    # state from investment_alignment.calculate() — demand rising faster than
    # coverage keeps pace, distinct from trend alone).
    emerging = [
        card for card in cards
        if card["cluster"].trend == "increasing" or card["alignment_state"] == "EMERGING_GAP"
    ]
    investment_gaps = [card for card in cards if card["alignment_state"] in _UNADDRESSED_STATES]
    pending = [card for card in cards if card["pending_decision"]]

    decisions_recorded = (
        GovernmentDecision.query.filter_by(country_id=country_id).count()
    )
    critical_count = sum(1 for card in cards if card["priority"] == "CRITICAL")

    your_projects = []
    if account.role in IMPLEMENTATION_ROLES:
        your_projects = (
            Project.query
            .filter_by(assigned_officer_id=account.id)
            .filter(Project.status != "Completion")
            .order_by(Project.updated_at.desc())
            .limit(10)
            .all()
        )

    mode = "implementation" if account.role == "department_officer" else "decision"

    return render_template(
        "government/dashboard.html",
        top_cards=top[:5],
        emerging_cards=emerging[:5],
        investment_gap_cards=investment_gaps[:5],
        pending_cards=pending[:5],
        country_code=account.country.code if account.country else "IN",
        mode=mode,
        role=account.role,
        read_only=account.role in EVIDENCE_ONLY_ROLES,
        stat_active_signals=len(cards),
        stat_critical_priority=critical_count,
        stat_awaiting_decision=len(pending),
        stat_decisions_recorded=decisions_recorded,
        your_projects=your_projects,
    )


# ---------------------------------------------------------------------------
# Screen 2 — Demand Map
# ---------------------------------------------------------------------------

@government_bp.route("/map")
@require_government_role(*_ALL_GOV_ROLES)
def demand_map():
    """Government demand intelligence map — same GeoJSON source as citizen map."""
    categories = Category.query.all()
    return render_template("government/demand_map.html", categories=categories)


# ---------------------------------------------------------------------------
# Screen 3 — Priority / Evidence Detail
# ---------------------------------------------------------------------------

@government_bp.route("/demand/<cluster_id>")
@require_government_role(*_ALL_GOV_ROLES)
def evidence_detail(cluster_id):
    """
    Single Priority Evidence Card.
    All scoring computed fresh per request — no caching, no background jobs.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    if not _cluster_in_scope(cluster, current_government_account()):
        abort(403)
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
@require_government_role(*_ALL_GOV_ROLES)
def decision_workspace(cluster_id):
    """
    Human decision workspace.

    GET  — show decision form with contextual action set based on role +
           current workflow state. Viewable by any government role
           (evidence-only roles included) for transparency.
    POST — record GovernmentDecision. Restricted to decision-authority and
           department_officer roles (Phase 0 §2) — analyst/reviewer are
           evidence-side only and are rejected here even if they somehow
           reach this branch (server-side, not just a hidden button).

    HARD INVARIANT: reason must come from request.form only.
    No AI-generated text may populate GovernmentDecision.reason.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    account = current_government_account()
    if not _cluster_in_scope(cluster, account):
        abort(403)
    role = current_role()

    existing_decision = (
        GovernmentDecision.query
        .filter_by(demand_cluster_id=cluster_id)
        .order_by(GovernmentDecision.timestamp.desc())
        .first()
    )

    if request.method == "POST":
        if role not in DECISION_AUTHORITY_ROLES and role != "department_officer":
            abort(403)

        decision_type = request.form.get("decision_type")
        # Reason sourced exclusively from the human-submitted form field.
        # AI must never be called to generate or populate this value.
        reason = (request.form.get("reason") or "").strip()
        linked_project_id = request.form.get("linked_project_id") or None

        if not reason:
            flash("A reason is required for every decision.", "error")
            return redirect(url_for("government.decision_workspace",
                                    cluster_id=cluster_id))

        valid_types_decision_authority = ("Prioritize", "Defer", "Deprioritize",
                                          "NeedsValidation", "Redirected")
        valid_types_department_officer = ("NeedsValidation", "Redirected", "Prioritize")
        valid = (
            valid_types_decision_authority if role in DECISION_AUTHORITY_ROLES
            else valid_types_department_officer
        )
        if decision_type not in valid:
            flash("Invalid decision type for your role.", "error")
            return redirect(url_for("government.decision_workspace",
                                    cluster_id=cluster_id))

        decision = GovernmentDecision(
            demand_cluster_id=cluster_id,
            country_id=cluster.country_id,
            decided_by_id=current_actor_id(),
            decided_by_account_id=account.id,
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
            report_lifecycle.mark_cluster_under_review(cluster_id)
        elif decision_type == "Defer":
            cluster.active_status = "Deferred"
        elif decision_type == "Deprioritize":
            cluster.active_status = "Deprioritized"

        # Finding 5 — append-only audit trail entry for this decision.
        # EventLog requires a report_id (NOT NULL per schema); attach this
        # event to one representative report in the cluster (the earliest
        # Contribution's report), since the event is fundamentally about the
        # cluster's decision, not any single report specifically.
        representative_contrib = (
            Contribution.query
            .filter_by(demand_cluster_id=cluster_id)
            .order_by(Contribution.timestamp.asc())
            .first()
        )
        if representative_contrib:
            db.session.add(EventLog(
                report_id=representative_contrib.report_id,
                demand_cluster_id=cluster_id,
                stage="Decision",
                metadata_={"decision_type": decision_type, "decided_by_role": role},
            ))

        log_action("government", account.id, "record_decision", "DemandCluster", cluster_id,
                   after_state={"decision_type": decision_type})
        db.session.commit()
        flash("Decision recorded.", "success")
        return redirect(url_for("government.evidence_detail",
                                cluster_id=cluster_id))

    # GET — collect available projects for the "Link project" dropdown
    projects = Project.query.filter_by(country_id=cluster.country_id).all()
    category = db.session.get(Category, cluster.category_id)

    return render_template(
        "government/decision_workspace.html",
        cluster=cluster,
        category=category,
        role=role,
        existing_decision=existing_decision,
        projects=projects,
    )


# ---------------------------------------------------------------------------
# Project / Outcome management
#
# Gap identified while verifying against BRICS_GOVERNMENT_MVP_FEATURES.md
# §2/§3/§20: the Government MVP Acceptance Test requires that a decision can
# be linked to a project (#9) and that implementation/outcome status can be
# followed (#10). The Decision Workspace previously only allowed linking to
# an already-existing Project — there was no route to create one, update its
# status, or record an Outcome, which left the Planning Officer's "Propose an
# action/project candidate", "Update implementation" and "Record outcome"
# actions (Government §3) completely unreachable in the UI. These three
# routes close that gap with the same minimal, human-authored-only spirit as
# the rest of the government blueprint.
# ---------------------------------------------------------------------------

@government_bp.route("/demand/<cluster_id>/propose-project", methods=["POST"])
@require_implementation_authority
def propose_project(cluster_id):
    """
    Create a new Project linked to this DemandCluster (Government §3 —
    Planning Officer: "Propose an action/project candidate"). If a
    GovernmentDecision already exists for this cluster, link it to the new
    project so the citizen timeline picks it up immediately. Assigned to the
    officer who created it (Project.assigned_officer_id) so it shows up in
    their "your projects in progress" dashboard section.
    """
    account = current_government_account()
    cluster = DemandCluster.query.get_or_404(cluster_id)
    if not _cluster_in_scope(cluster, account):
        abort(403)
    name = (request.form.get("project_name") or "").strip()
    expected_completion_raw = (request.form.get("expected_completion") or "").strip()

    if not name:
        flash("A project name is required.", "error")
        return redirect(url_for("government.decision_workspace", cluster_id=cluster_id))

    expected_completion = None
    if expected_completion_raw:
        from datetime import date
        try:
            expected_completion = date.fromisoformat(expected_completion_raw)
        except ValueError:
            pass  # left as None — never guess a date

    region_id = (cluster.region_ids or [None])[0]
    project = Project(
        name=name,
        country_id=cluster.country_id,
        region_id=region_id,
        assigned_officer_id=account.id,
        department_id=account.department_id,
        status="Planning",
        milestones=[],
        linked_demand_cluster_id=cluster_id,
        expected_completion=expected_completion,
    )
    db.session.add(project)
    db.session.flush()

    latest_decision = (
        GovernmentDecision.query
        .filter_by(demand_cluster_id=cluster_id)
        .order_by(GovernmentDecision.timestamp.desc())
        .first()
    )
    if latest_decision:
        latest_decision.linked_project_id = project.id

    report_lifecycle.mark_cluster_actioned(cluster_id)
    log_action("government", account.id, "propose_project", "Project", project.id,
               after_state={"name": name, "linked_demand_cluster_id": cluster_id})
    db.session.commit()
    flash(f'Project "{name}" created and linked to this demand.', "success")
    return redirect(url_for("government.evidence_detail", cluster_id=cluster_id))


@government_bp.route("/projects/<project_id>/update-status", methods=["POST"])
@require_implementation_authority
def update_project_status(project_id):
    """
    Update a Project's implementation status and optionally append a
    milestone (Government §3 — Planning Officer: "Update implementation").
    Milestones are appended, never rewritten — matches Project.milestones'
    "no invented dates" rule (Government §16 / models/government_models.py).
    """
    project = Project.query.get_or_404(project_id)
    new_status = request.form.get("status")
    valid_statuses = ("Planning", "Approval", "Tender", "Construction", "Completion")
    if new_status not in valid_statuses:
        flash("Invalid project status.", "error")
        return redirect(url_for("government.projects_outcomes"))

    project.status = new_status

    milestone_label = (request.form.get("milestone_label") or "").strip()
    if milestone_label:
        milestones = list(project.milestones or [])
        milestones.append({"label": milestone_label, "status": "done"})
        project.milestones = milestones

    # EventLog: Implementation stage. Attach to a representative report the
    # same way the Decision stage does (Finding 5 pattern) — EventLog.report_id
    # is NOT NULL and this event is about the project, not a single report.
    if project.linked_demand_cluster_id:
        representative_contrib = (
            Contribution.query
            .filter_by(demand_cluster_id=project.linked_demand_cluster_id)
            .order_by(Contribution.timestamp.asc())
            .first()
        )
        if representative_contrib:
            db.session.add(EventLog(
                report_id=representative_contrib.report_id,
                demand_cluster_id=project.linked_demand_cluster_id,
                stage="Implementation",
                metadata_={"project_status": new_status, "project_id": project.id},
            ))

    log_action("government", current_government_account().id, "update_project_status",
               "Project", project.id, after_state={"status": new_status})
    db.session.commit()
    flash("Project status updated.", "success")
    return redirect(url_for("government.projects_outcomes"))


@government_bp.route("/projects/<project_id>/outcome", methods=["POST"])
@require_implementation_authority
def record_outcome(project_id):
    """
    Record or update the Outcome for a Project (Government §3 — Planning
    Officer: "Record outcome").

    Hard invariant preserved (Progress Log §13.4 / models/government_models.py):
    status only becomes 'Verified' when a human explicitly checks the
    verification box AND provides after_indicator text — it is never
    auto-filled or inferred from project status. Completion of a Project
    does NOT imply the Outcome is verified.
    """
    project = Project.query.get_or_404(project_id)
    before_indicator = (request.form.get("before_indicator") or "").strip() or None
    after_indicator = (request.form.get("after_indicator") or "").strip() or None
    impact_percent_raw = (request.form.get("impact_percent") or "").strip()
    verified = request.form.get("verified") == "on"

    impact_percent = None
    if impact_percent_raw:
        try:
            impact_percent = float(impact_percent_raw)
        except ValueError:
            pass

    outcome = Outcome.query.filter_by(project_id=project_id).first()
    if outcome is None:
        outcome = Outcome(
            project_id=project_id,
            demand_cluster_id=project.linked_demand_cluster_id,
        )
        db.session.add(outcome)

    outcome.before_indicator = before_indicator or outcome.before_indicator
    outcome.after_indicator = after_indicator
    outcome.impact_percent = impact_percent
    # Never auto-verify — only a human checking the box, with real indicator
    # text present, can move this out of AwaitingOutcomeData.
    outcome.status = "Verified" if (verified and after_indicator) else "AwaitingOutcomeData"

    if project.linked_demand_cluster_id:
        representative_contrib = (
            Contribution.query
            .filter_by(demand_cluster_id=project.linked_demand_cluster_id)
            .order_by(Contribution.timestamp.asc())
            .first()
        )
        if representative_contrib:
            db.session.add(EventLog(
                report_id=representative_contrib.report_id,
                demand_cluster_id=project.linked_demand_cluster_id,
                stage="Outcome",
                metadata_={"outcome_status": outcome.status, "project_id": project.id},
            ))

    if outcome.status == "Verified" and project.linked_demand_cluster_id:
        report_lifecycle.mark_cluster_resolved(project.linked_demand_cluster_id)

    log_action("government", current_government_account().id, "record_outcome",
               "Outcome", outcome.id, after_state={"status": outcome.status})
    db.session.commit()
    flash("Outcome recorded.", "success")
    return redirect(url_for("government.projects_outcomes"))


# ---------------------------------------------------------------------------
# Screen 5 — Projects / Outcomes
# ---------------------------------------------------------------------------

@government_bp.route("/projects")
@require_government_role(*_ALL_GOV_ROLES)
def projects_outcomes():
    """Linked interventions and progress/outcome status — real aggregate
    stats (Total/In progress/Completed/Verified outcomes), matching the
    supplied project-outcome.png design."""
    account = current_government_account()
    country_id = account.country_id

    projects = (
        Project.query
        .filter_by(country_id=country_id)
        .order_by(Project.updated_at.desc())
        .all()
    )
    allowed_region_ids = scoped_region_ids(account)
    if allowed_region_ids is not None:
        allowed_set = set(allowed_region_ids)
        projects = [p for p in projects if p.region_id is None or p.region_id in allowed_set]

    project_data = []
    verified_outcomes = 0
    in_progress = 0
    completed = 0
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
        if outcome and outcome.status == "Verified":
            verified_outcomes += 1
        if p.status == "Completion":
            completed += 1
        else:
            in_progress += 1

    return render_template(
        "government/projects_outcomes.html",
        project_data=project_data,
        role=current_role(),
        stat_total_projects=len(project_data),
        stat_in_progress=in_progress,
        stat_completed=completed,
        stat_verified_outcomes=verified_outcomes,
    )


# ---------------------------------------------------------------------------
# Screen 6 — Admin
# ---------------------------------------------------------------------------

@government_bp.route("/admin", methods=["GET", "POST"])
@require_government_role("national_admin")
def admin():
    """
    Technical configuration only — no policy authority (Progress Log §5.1).
    Real accounts now (Phase 0) — countries/categories/government accounts,
    not a demo actor registry.
    """
    countries = Country.query.all()
    categories = Category.query.all()
    accounts = GovernmentAccount.query.order_by(GovernmentAccount.role, GovernmentAccount.email).all()

    return render_template(
        "government/admin.html",
        countries=countries,
        categories=categories,
        accounts=accounts,
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
