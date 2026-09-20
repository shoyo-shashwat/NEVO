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

import json
from datetime import datetime, timezone

from flask import render_template, request, session, redirect, url_for, flash
from sqlalchemy import func, or_

from app.government import government_bp
from app.extensions import db
from app.auth.session import require_role, current_actor_id, current_role, current_user

# Models — citizen-originated data read directly from models/, no citizen/ import
from app.models.demand_cluster import DemandCluster
from app.models.citizen_models import Contribution, Verification  # read-only counts
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.models.reference_data import InfrastructureDataPoint, GovernmentInvestment
from app.models.shared import Category, Country, AdministrativeRegion, EventLog
from app.models.auth_models import GovernmentAccount, Department, log_action

# Services — scoring lives here only, never inlined in routes
from app.services import gap_assessment, investment_alignment, priority_scoring, development_insight, alignment_analytics


# ---------------------------------------------------------------------------
# Screen 1 — Dashboard
# ---------------------------------------------------------------------------

_PRIORITY_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
_UNADDRESSED_STATES = ("UNADDRESSED", "PARTIALLY_ADDRESSED", "IMPLEMENTATION_ACCESS_GAP")


@government_bp.route("/dashboard")
@require_role("mp", "planning_officer", "reviewer")
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

    Planning Officer's real home is now national_overview() (India-wide
    lens) — redirect here so an old link/bookmark lands on the right page
    rather than the MP-shaped constituency dashboard.
    """
    if current_role() == "planning_officer":
        return redirect(url_for("government.national_overview"))

    country_code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=country_code).first()
    country_id = country.id if country else None

    # MP scope resolved once, up front — every pool below (cards, the four
    # lenses, and the stat strip) must be built from the SAME scoped set, not
    # just the Investment Alignment card. Missing this here meant an MP's
    # "Active signals"/"Critical priority"/Top Priorities list were silently
    # country-wide while only the alignment breakdown was constituency-scoped
    # — a real cross-state data leak caught during multi-state browser
    # verification, not a cosmetic gap.
    mp_region_id, mp_region_ids, _ = _government_scope(country_id)

    # Active clusters for this country (and, for an MP, their own
    # constituency), ordered by trend then recency.
    cluster_query = DemandCluster.query.filter(
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
        DemandCluster.country_id == country_id,
    )
    clusters = cluster_query.order_by(DemandCluster.updated_at.desc()).limit(200).all()
    clusters = [c for c in clusters if _in_mp_scope(c.region_ids, mp_region_ids)]
    clusters = clusters[:20]

    cards = [_build_cluster_card(c) for c in clusters]

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

    # Round 4 UI fix — "the reduction [redirection] is not right, MP should
    # see only MP issues, officer to officer": before this, MP and Planning
    # Officer landed on the literal same dashboard.html render with identical
    # sections, because require_role("mp", "planning_officer") gates access
    # to the route but nothing inside it branched on *which* of the two was
    # logged in. That's the actual bug behind the report. Fixed here by
    # scoping which of the four lenses each role sees, matching the two
    # roles' real responsibilities elsewhere in this file:
    #   - MP owns Prioritize/Defer/Deprioritize (decision_workspace's
    #     valid_types_mp) — a strategic, prioritization view: Top Priorities,
    #     Emerging Gaps, and decisions awaiting their sign-off.
    #   - Planning Officer owns validation + implementation (propose_project,
    #     update_project_status, record_outcome) — an operational view:
    #     Investment/Intervention Gaps (where a project might be needed),
    #     decisions awaiting validation, and their in-flight projects.
    # Both still read from the exact same `cards` pool computed above — this
    # is a display-scoping change, not a new scoring rule or schema change.
    role = current_role()
    in_progress_projects = []
    if role == "planning_officer":
        in_progress_projects = (
            Project.query
            .filter(Project.country_id == country_id, Project.status != "Completion")
            .order_by(Project.updated_at.desc())
            .limit(5)
            .all()
        )

    # decisions_recorded: all-time count, but scoped to the MP's own
    # constituency clusters when applicable — GovernmentDecision has no
    # region of its own, so scoping means counting decisions whose cluster's
    # region_ids overlap the same mp_region_ids used everywhere else on this
    # page (a plain-JSON column, so the overlap check happens in Python,
    # same pattern demand_intelligence()/citizen_voice() already use).
    if mp_region_ids and country_id:
        decision_cluster_ids = (
            db.session.query(DemandCluster.id, DemandCluster.region_ids)
            .join(GovernmentDecision, GovernmentDecision.demand_cluster_id == DemandCluster.id)
            .filter(GovernmentDecision.country_id == country_id)
            .all()
        )
        decisions_recorded = len([
            row for row in decision_cluster_ids if _in_mp_scope(row.region_ids, mp_region_ids)
        ])
    else:
        decisions_recorded = GovernmentDecision.query.filter_by(country_id=country_id).count() if country_id else 0

    # Quick-glance stat strip — same `cards` pool, just tallied. Purely
    # presentational (Round 4 "dashboard is very empty" feedback); no new
    # scoring beyond the scoped decision count above.
    stats = {
        "active_signals": len(cards),
        "critical_count": len([c for c in cards if c["priority"] == "CRITICAL"]),
        "awaiting_decision": len(pending),
        "decisions_recorded": decisions_recorded,
    }

    # Population-based investment-alignment breakdown (India-only MVP design
    # decision — always the five states as separate percentages, never one
    # collapsed "coverage score"). MP sees it scoped to their own
    # constituency (GovernmentAccount.region_id); Planning Officer sees the
    # country-wide picture — the same "my constituency" vs "India" split
    # that scopes the four lenses above.
    breakdown = None
    if country_id:
        breakdown = alignment_analytics.calculate(country_id, region_id=mp_region_id)

    return render_template(
        "government/dashboard.html",
        role=role,
        top_cards=top[:5],
        emerging_cards=emerging[:5],
        investment_gap_cards=investment_gaps[:5],
        pending_cards=pending[:5],
        in_progress_projects=in_progress_projects,
        stats=stats,
        country_code=country_code,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Screen 2 — Demand Map
# ---------------------------------------------------------------------------

@government_bp.route("/map")
@require_role("mp", "planning_officer", "reviewer")
def demand_map():
    """
    Government demand intelligence map — same GeoJSON source as citizen map,
    same per-country view (services/map_view.py) so both sides of the app
    agree on what "your country's map" means.
    """
    from app.services.map_view import get_map_view

    categories = Category.query.all()
    country_code = session.get("country_code", "IN")
    return render_template(
        "government/demand_map.html",
        categories=categories,
        country_code=country_code,
        map_view=get_map_view(country_code),
    )


@government_bp.route("/map/projects-data")
@require_role("mp", "planning_officer", "reviewer")
def demand_map_projects_data():
    """
    GeoJSON endpoint for the Development Map's Projects layer (P0 —
    markers/heatmap only, no boundary-polygon dependency; see the
    India-only MVP design review's Atlas-vs-Map distinction).

    Project has no geometry of its own (only an optional region_id, and
    AdministrativeRegion itself has no stored coordinates — see
    app/models/shared.py). Plotting a project at a fabricated "region
    centroid" would be exactly the kind of invented precision this
    codebase deliberately avoids elsewhere (gap_assessment.py,
    investment_alignment.py). Instead, only projects with a
    linked_demand_cluster_id are plotted — at that cluster's real,
    citizen-report-derived centroid — since "this project addresses the
    demand located here" is a claim the data actually supports. Projects
    without a linked cluster are omitted from the map (not fabricated a
    location), same discipline as unmatched regions in gap_assessment.py.
    """
    from geoalchemy2.functions import ST_AsGeoJSON

    country_id = _country_id_from_session()

    rows = (
        db.session.query(
            Project.id,
            Project.name,
            Project.status,
            DemandCluster.category_id,
            DemandCluster.region_ids,
            ST_AsGeoJSON(DemandCluster.centroid).label("geojson"),
        )
        .join(DemandCluster, Project.linked_demand_cluster_id == DemandCluster.id)
        .filter(
            Project.country_id == country_id,
            DemandCluster.centroid.isnot(None),
        )
        .all()
    )

    # MP-scoped to their own constituency (same rule every other government
    # list view applies via _government_scope()) — this endpoint was the one
    # place that scoping had been missed, so an MP's map used to silently
    # show every state's project markers instead of just their own.
    _, mp_region_ids, _ = _government_scope(country_id)
    rows = [r for r in rows if _in_mp_scope(r.region_ids, mp_region_ids)]

    features = []
    for row in rows:
        if not row.geojson:
            continue
        cat = db.session.get(Category, row.category_id)
        features.append({
            "type": "Feature",
            "geometry": json.loads(row.geojson),
            "properties": {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "category_code": cat.code if cat else "",
            },
        })

    return {"type": "FeatureCollection", "features": features}


def _country_id_from_session() -> str:
    code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=code).first()
    return country.id if country else None


def _government_scope(country_id: str):
    """
    Resolve the current government session's geographic scope, in one place.

    Returns (mp_region_id, expanded_region_ids, region_name):
      - MP: (their own GovernmentAccount.region_id, that region + every
        administrative descendant as a set, region name) — a cluster/report
        scoped only to a district beneath the MP's state-level region still
        counts as theirs. mp_region_id (unexpanded) is what
        alignment_analytics.calculate(region_id=...) wants, since that
        function does its own descendant expansion internally;
        expanded_region_ids is what Python-side "is this cluster/report in
        scope" membership checks want.
      - Planning Officer / reviewer / admin: (None, None, None) — no
        restriction, country-wide.

    Every route below used to re-derive this independently ("if role ==
    mp: get GovernmentAccount.region_id..."), and one of those four
    independent copies used an exact-match instead of descendant-inclusive
    matching — a real bug that silently dropped legitimate results before
    it was caught. Centralizing it here means fixing the scoping rule once
    fixes it everywhere, instead of relying on every call site staying in
    sync by hand.
    """
    if current_role() != "mp":
        return None, None, None
    actor = current_user()
    mp_region_id = getattr(actor, "region_id", None)
    if not mp_region_id:
        return None, None, None
    expanded_region_ids = alignment_analytics._descendant_region_ids(country_id, mp_region_id)
    region = db.session.get(AdministrativeRegion, mp_region_id)
    return mp_region_id, expanded_region_ids, (region.name if region else None)


def _cluster_in_mp_scope(cluster: DemandCluster) -> bool:
    """
    Authorization check, not just a list filter: an MP session must not be
    able to view or act on a cluster outside their own constituency by
    guessing/typing its URL directly. demand_intelligence()/citizen_voice()/
    reports() already filter their *lists* to the MP's scope, but a direct
    GET to /gov/demand/<cluster_id> or /gov/demand/<cluster_id>/decide
    previously had no such check at all -- flagged in the post-Planning-
    Officer-split hardening review. Planning Officer/reviewer are always
    True (country-wide scope, no restriction).

    A cluster with no region_ids at all (data gap, not a real region) is
    treated as in-scope rather than silently 404/403ing an MP out of a
    cluster that simply hasn't been geo-tagged yet.
    """
    if current_role() != "mp":
        return True
    country_id = cluster.country_id
    _, mp_region_ids, _ = _government_scope(country_id)
    if not mp_region_ids:
        return True  # MP account has no region_id set -- nothing to restrict against
    cluster_regions = set(cluster.region_ids or [])
    if not cluster_regions:
        return True
    return bool(mp_region_ids & cluster_regions)


def _in_mp_scope(region_ids, mp_region_ids) -> bool:
    """
    Same "no region_ids = in scope, not filtered out" rule as
    _cluster_in_mp_scope(), factored out for the many list-building call
    sites below (dashboard/reports/policy_insights/map/etc.) that used to
    each do a raw `mp_region_ids & set(c.region_ids or [])` check — which
    silently hid any not-yet-geo-tagged cluster from its own MP, the same
    class of bug fixed on the citizen side (see
    citizen/routes.py::_in_region_scope()).
    """
    if not mp_region_ids:
        return True
    ids = set(region_ids or [])
    if not ids:
        return True
    return bool(ids & mp_region_ids)


# ---------------------------------------------------------------------------
# Demand Intelligence — shared list view (MP: constituency-scoped via
# GovernmentAccount.region_id; Planning Officer: country-wide). Same
# underlying cluster-card data as the dashboard's four lenses, but here as a
# single filterable/searchable list rather than four fixed groupings — the
# piece neither role had before this (India-only MVP design review).
# ---------------------------------------------------------------------------

@government_bp.route("/demand-intelligence")
@require_role("mp", "planning_officer", "reviewer")
def demand_intelligence():
    country_id = _country_id_from_session()
    role = current_role()

    sector_filter = request.args.get("sector") or None
    alignment_filter = request.args.get("alignment") or None
    status_filter = request.args.get("status") or None

    query = DemandCluster.query.filter(DemandCluster.country_id == country_id)
    if sector_filter:
        query = query.filter(DemandCluster.category_id == sector_filter)
    if status_filter:
        query = query.filter(DemandCluster.active_status == status_filter)
    else:
        query = query.filter(DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]))

    # region_ids is a plain JSON column (not JSONB), so Postgres has no `@>`
    # containment operator for it — filtering happens in Python after the
    # fetch, same pattern as the alignment_filter below.
    _, mp_region_ids, region_note = _government_scope(country_id)

    clusters = query.order_by(DemandCluster.updated_at.desc()).limit(200).all()
    clusters = [c for c in clusters if _in_mp_scope(c.region_ids, mp_region_ids)][:100]
    cards = [_build_cluster_card(c) for c in clusters]

    # Alignment filter applied after card-building since alignment_state is
    # computed fresh, not a stored column.
    if alignment_filter:
        cards = [c for c in cards if c["alignment_state"] == alignment_filter]

    categories = Category.query.all()

    return render_template(
        "government/demand_intelligence.html",
        cards=cards,
        categories=categories,
        role=role,
        region_note=region_note,
        sector_filter=sector_filter,
        alignment_filter=alignment_filter,
        status_filter=status_filter,
    )


# ---------------------------------------------------------------------------
# National Overview — Planning Officer's home screen. Distinct from MP's
# "my constituency" dashboard.html: leads with India-wide demand
# distribution and the alignment breakdown, not a constituency KPI strip.
# See the India-only MVP design review's MP-vs-Planning-Officer information
# architecture (different scope/lens over the same Demand Cluster
# Intelligence object, not a separate implementation of it).
# ---------------------------------------------------------------------------

@government_bp.route("/national-overview")
@require_role("planning_officer")
def national_overview():
    country_id = _country_id_from_session()

    clusters = (
        DemandCluster.query
        .filter(
            DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
            DemandCluster.country_id == country_id,
        )
        .all()
    )

    # Sector distribution — real counts, category name resolved once per
    # category rather than per cluster.
    sector_counts: dict = {}
    for c in clusters:
        sector_counts[c.category_id] = sector_counts.get(c.category_id, 0) + 1
    categories = {cat.id: cat for cat in Category.query.all()}
    sector_distribution = sorted(
        [
            {"name": categories[cat_id].name, "count": count}
            for cat_id, count in sector_counts.items() if cat_id in categories
        ],
        key=lambda x: x["count"], reverse=True,
    )

    stats = {
        "active_clusters": len(clusters),
        "increasing": len([c for c in clusters if c.trend == "increasing"]),
        "decisions_recorded": GovernmentDecision.query.filter_by(country_id=country_id).count() if country_id else 0,
        "projects_total": Project.query.filter_by(country_id=country_id).count() if country_id else 0,
        "projects_completed": Project.query.filter_by(country_id=country_id, status="Completion").count() if country_id else 0,
    }

    breakdown = alignment_analytics.calculate(country_id) if country_id else None

    # "Needs attention" — same pool, top 5 by priority, reusing the shared
    # card builder (kept small: this is a summary screen, not the full list —
    # that's demand_intelligence()).
    needs_attention = sorted(
        [_build_cluster_card(c) for c in clusters[:30]],  # cap: summary screen, not exhaustive
        key=lambda card: _PRIORITY_RANK.get(card["priority"], 0),
        reverse=True,
    )[:5]

    return render_template(
        "government/national_overview.html",
        stats=stats,
        sector_distribution=sector_distribution,
        breakdown=breakdown,
        needs_attention=needs_attention,
        country_code=session.get("country_code", "IN"),
    )


# ---------------------------------------------------------------------------
# Investment Alignment — the five-state breakdown made a real analytical
# surface (per the India-only MVP design review: "make this the backbone of
# the government UI" rather than inventing another scoring framework).
# Country-wide overall, plus a per-sector breakdown table so a Planning
# Officer can see e.g. "healthcare is 70% unaddressed" at a glance.
# ---------------------------------------------------------------------------

@government_bp.route("/investment-alignment")
@require_role("mp", "planning_officer", "reviewer")
def investment_alignment_view():
    country_id = _country_id_from_session()

    overall = alignment_analytics.calculate(country_id) if country_id else None

    categories = Category.query.all()
    by_sector = []
    for cat in categories:
        breakdown = alignment_analytics.calculate(country_id, category_id=cat.id) if country_id else None
        if breakdown and breakdown.cluster_count:
            by_sector.append({"category": cat, "breakdown": breakdown})
    by_sector.sort(key=lambda row: row["breakdown"].cluster_count, reverse=True)

    return render_template(
        "government/investment_alignment.html",
        overall=overall,
        by_sector=by_sector,
    )


# ---------------------------------------------------------------------------
# Gap Analysis — "Development Gap Explorer": pick a sector + region, see the
# evidence. Reuses gap_assessment.py / investment_alignment.py directly
# against a synthetic query rather than a stored DemandCluster, since a
# Planning Officer is exploring "what does the evidence say about this
# sector+region combination" even where no cluster happens to exist yet.
# ---------------------------------------------------------------------------

@government_bp.route("/gap-analysis")
@require_role("mp", "planning_officer", "reviewer")
def gap_analysis():
    country_id = _country_id_from_session()
    categories = Category.query.all()
    regions = AdministrativeRegion.query.filter_by(country_id=country_id).order_by(AdministrativeRegion.name).all() if country_id else []

    sector_id = request.args.get("sector") or None
    region_id = request.args.get("region") or None

    result = None
    if sector_id and region_id:
        # Reuse the same region-matching machinery gap_assessment.py uses,
        # but against a single region rather than a cluster's region_ids —
        # this endpoint has no cluster to read region_ids from, so it asks
        # the question directly: "what evidence exists for this sector in
        # this region (and its administrative ancestors)?"
        from app.services.region_matching import fetch_ancestor_chains, resolve_region_matches
        from app.models.reference_data import InfrastructureDataPoint, DemographicDataPoint

        chains = fetch_ancestor_chains([region_id])
        infra_candidates = InfrastructureDataPoint.query.filter_by(category_id=sector_id, country_id=country_id).all()
        demo_candidates = DemographicDataPoint.query.filter_by(category_id=sector_id, country_id=country_id).all()
        infra_match = resolve_region_matches([region_id], infra_candidates, chains)
        demo_match = resolve_region_matches([region_id], demo_candidates, chains)

        # Clusters already located in this region+sector, for demand context
        # and investment relevance — same region-relevance filter investment_
        # alignment.py uses, applied directly here since there's no single
        # cluster to hand to that service.
        from app.services.investment_alignment import _filter_relevant_investments
        from app.models.reference_data import GovernmentInvestment
        inv_candidates = GovernmentInvestment.query.filter_by(category_id=sector_id, country_id=country_id).all()
        relevant_investments, investment_scope = _filter_relevant_investments(
            inv_candidates, region_ids=[region_id], chains=chains,
        )

        matching_clusters = [
            c for c in DemandCluster.query.filter_by(category_id=sector_id, country_id=country_id).all()
            if region_id in (c.region_ids or [])
        ]

        result = {
            "sector": db.session.get(Category, sector_id),
            "region": db.session.get(AdministrativeRegion, region_id),
            "infra_row": infra_match.matched_rows[0] if infra_match.matched_rows else None,
            "infra_scope": infra_match.scope_label,
            "demo_row": demo_match.matched_rows[0] if demo_match.matched_rows else None,
            "demo_scope": demo_match.scope_label,
            "investments": relevant_investments,
            "investment_scope": investment_scope,
            "clusters": matching_clusters,
            "total_reports": sum(c.total_reports for c in matching_clusters),
            "total_contributors": sum(c.unique_contributors for c in matching_clusters),
        }

    return render_template(
        "government/gap_analysis.html",
        categories=categories,
        regions=regions,
        sector_id=sector_id,
        region_id=region_id,
        result=result,
    )


# ---------------------------------------------------------------------------
# Policy Insights — Planning Officer's highest-level page. Reuses
# development_insight.py (already evidence-composed, never a fresh LLM
# call) across the top-priority clusters nationally, rather than
# introducing a second "insight" concept. Explicitly not framed as "AI
# tells government what to do" (India-only MVP design review) — each card
# is Insight + underlying evidence + link to the actual cluster.
# ---------------------------------------------------------------------------

@government_bp.route("/policy-insights")
@require_role("planning_officer", "mp", "reviewer")
def policy_insights():
    country_id = _country_id_from_session()

    # MP-scoped to their own constituency's clusters, same rule as every
    # other government list view (this route allows "mp" too, and had no
    # scoping at all).
    _, mp_region_ids, _ = _government_scope(country_id)

    clusters = (
        DemandCluster.query
        .filter(
            DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
            DemandCluster.country_id == country_id,
        )
        .order_by(DemandCluster.updated_at.desc())
        .limit(200)
        .all()
    )
    clusters = [c for c in clusters if _in_mp_scope(c.region_ids, mp_region_ids)]
    clusters = clusters[:30]

    cards = [_build_cluster_card(c) for c in clusters]
    top_cards = sorted(cards, key=lambda card: _PRIORITY_RANK.get(card["priority"], 0), reverse=True)[:8]

    insights = []
    for card in top_cards:
        cluster = card["cluster"]
        gap = gap_assessment.calculate(cluster.id)
        alignment = investment_alignment.calculate(cluster.id, gap_assessment=gap)
        insight = development_insight.calculate(cluster.id, gap=gap, alignment=alignment)
        insights.append({
            "cluster": cluster,
            "category_name": card["category_name"],
            "category_code": card["category_code"],
            "priority": card["priority"],
            "alignment_state": alignment.state,
            "insight": insight,
        })

    return render_template("government/policy_insights.html", insights=insights)


# ---------------------------------------------------------------------------
# Citizen Voice — deliberately separate from Demand Intelligence (India-only
# MVP design review): Demand Intelligence answers "what does the aggregated
# data show", Citizen Voice answers "what are constituents actually
# saying." Raw problem_summary_en text (the multilingual "common format"
# field — see citizen/routes.py module docstring), never the aggregated
# evidence. MP-scoped to their own constituency's region when the account
# has one set; Planning Officer sees the country-wide feed.
# ---------------------------------------------------------------------------

@government_bp.route("/citizen-voice")
@require_role("mp", "planning_officer", "reviewer")
def citizen_voice():
    from app.models.citizen_models import Report
    from datetime import timedelta

    country_id = _country_id_from_session()
    role = current_role()

    query = (
        Report.query
        .filter(Report.country_id == country_id, Report.problem_summary_en.isnot(None))
    )

    _, mp_region_ids, region_note = _government_scope(country_id)
    if mp_region_ids:
        # region_id IS NULL (location text never resolved to a region) counts
        # as in-scope too — same "don't hide a citizen's own report from
        # their own MP over a data gap" rule as _in_mp_scope() below.
        query = query.filter(or_(Report.region_id.in_(mp_region_ids), Report.region_id.is_(None)))

    recent_reports = query.order_by(Report.created_at.desc()).limit(30).all()

    report_cards = []
    for r in recent_reports:
        cat = db.session.get(Category, r.category_id) if r.category_id else None
        report_cards.append({
            "report": r,
            "category_name": cat.name if cat else "",
        })

    # Emerging themes — real counts, worded as "increased over the period",
    # never "trend" (that would imply statistical significance this simple
    # count comparison doesn't establish). Last 30 days vs the 30 days
    # before that, same country/region scope as the feed above.
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=30)
    prior_start = now - timedelta(days=60)

    def _category_counts(start, end):
        q = (
            db.session.query(Report.category_id, func.count(Report.id))
            .filter(Report.country_id == country_id, Report.created_at >= start, Report.created_at < end)
        )
        if mp_region_ids:
            q = q.filter(or_(Report.region_id.in_(mp_region_ids), Report.region_id.is_(None)))
        return dict(q.group_by(Report.category_id).all())

    recent_counts = _category_counts(window_start, now)
    prior_counts = _category_counts(prior_start, window_start)

    emerging_themes = []
    categories_by_id = {c.id: c for c in Category.query.all()}
    for cat_id, recent_count in recent_counts.items():
        prior_count = prior_counts.get(cat_id, 0)
        if recent_count >= 3 and recent_count > prior_count:
            cat = categories_by_id.get(cat_id)
            if cat:
                emerging_themes.append({
                    "category_name": cat.name,
                    "recent_count": recent_count,
                    "prior_count": prior_count,
                })
    emerging_themes.sort(key=lambda t: t["recent_count"] - t["prior_count"], reverse=True)

    return render_template(
        "government/citizen_voice.html",
        report_cards=report_cards,
        region_note=region_note,
        role=role,
        emerging_themes=emerging_themes[:3],
    )


# ---------------------------------------------------------------------------
# Reports — Constituency/National Development Brief. P2 per the India-only
# MVP design review ("presentation output, not proof of the core
# intelligence — build it once the intelligence screens are good"). HTML
# on-screen, print-to-PDF via the browser rather than a document-generation
# engine — same MVP scope call the design review made.
# ---------------------------------------------------------------------------

@government_bp.route("/reports")
@require_role("mp", "planning_officer", "reviewer")
def reports():
    country_id = _country_id_from_session()
    role = current_role()

    mp_region_id, mp_region_ids, region_note = _government_scope(country_id)

    clusters = DemandCluster.query.filter(
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
        DemandCluster.country_id == country_id,
    ).all()
    clusters = [c for c in clusters if _in_mp_scope(c.region_ids, mp_region_ids)]

    cards = [_build_cluster_card(c) for c in clusters]
    top_cards = sorted(cards, key=lambda card: _PRIORITY_RANK.get(card["priority"], 0), reverse=True)[:10]
    unaddressed = [card for card in cards if card["alignment_state"] in _UNADDRESSED_STATES]

    breakdown = alignment_analytics.calculate(country_id, region_id=mp_region_id) if country_id else None

    # Project.region_id is a single value (unlike DemandCluster.region_ids),
    # so an MP's scope check is a plain IN filter — same mp_region_ids used
    # to scope clusters above, so a state's Development Brief only ever
    # counts that state's own projects/outcomes, not every project in India.
    # region_id IS NULL (no region assigned yet) still counts as in-scope —
    # same data-gap rule as everywhere else, not excluded.
    project_query = Project.query.filter_by(country_id=country_id)
    if mp_region_ids:
        project_query = project_query.filter(or_(Project.region_id.in_(mp_region_ids), Project.region_id.is_(None)))
    projects_completed = project_query.filter_by(status="Completion").count()
    projects_total = project_query.count()

    outcomes_query = (
        Outcome.query.join(Project, Outcome.project_id == Project.id)
        .filter(Project.country_id == country_id, Outcome.status == "Verified")
    )
    if mp_region_ids:
        outcomes_query = outcomes_query.filter(or_(Project.region_id.in_(mp_region_ids), Project.region_id.is_(None)))
    outcomes_verified = outcomes_query.count()

    return render_template(
        "government/reports.html",
        role=role,
        region_note=region_note,
        generated_at=datetime.now(timezone.utc),
        stats={
            "total_demands": len(clusters),
            "total_reports": sum(c.total_reports for c in clusters),
            "total_contributors": sum(c.unique_contributors for c in clusters),
            "unaddressed_count": len(unaddressed),
            "projects_total": projects_total,
            "projects_completed": projects_completed,
            "outcomes_verified": outcomes_verified,
        },
        breakdown=breakdown,
        top_cards=top_cards,
        unaddressed=unaddressed[:10],
    )


# ---------------------------------------------------------------------------
# Screen 3 — Priority / Evidence Detail
# ---------------------------------------------------------------------------

@government_bp.route("/demand/<cluster_id>")
@require_role("mp", "planning_officer", "reviewer")
def evidence_detail(cluster_id):
    """
    Single Priority Evidence Card.
    All scoring computed fresh per request — no caching, no background jobs.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    if not _cluster_in_mp_scope(cluster):
        flash("This demand is outside your constituency.", "error")
        return redirect(url_for("government.dashboard"))
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
    insight = development_insight.calculate(cluster_id, gap=gap, alignment=alignment)

    from app.services import demand_evolution
    evolution = demand_evolution.calculate(cluster_id)

    # Existing decision if any
    existing_decision = (
        GovernmentDecision.query
        .filter_by(demand_cluster_id=cluster_id)
        .order_by(GovernmentDecision.timestamp.desc())
        .first()
    )

    # Multilingual architecture: citizen reports arrive in the citizen's own
    # language and stay that way (original_raw_input is never translated —
    # see app/models/citizen_models.py). Until now a government reviewer had
    # no way to see what was actually reported, in any language — evidence_
    # detail only ever showed aggregate numbers. problem_summary_en is the
    # "common format" structured field groq_client.extract_report_fields()
    # produces for exactly this — a handful of representative ones here
    # give an officer real, readable-in-English context regardless of the
    # underlying reports' language.
    from app.models.citizen_models import Report
    # Fetch a larger pool and dedupe by text in Python (not a DB-level
    # DISTINCT, to stay portable) -- otherwise several citizens reporting
    # the same problem in near-identical words (common for a templated
    # demo, and plausible for real citizens describing the same visible
    # issue) shows as the same quote repeated 3 times, which reads as a
    # bug even though each row is a real report.
    _candidate_reports = (
        Report.query
        .join(Contribution, Contribution.report_id == Report.id)
        .filter(Contribution.demand_cluster_id == cluster_id, Report.problem_summary_en.isnot(None))
        .order_by(Report.created_at.desc())
        .limit(20)
        .all()
    )
    sample_reports = []
    _seen_texts = set()
    for r in _candidate_reports:
        if r.problem_summary_en not in _seen_texts:
            _seen_texts.add(r.problem_summary_en)
            sample_reports.append(r)
        if len(sample_reports) == 3:
            break

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
        sample_reports=sample_reports,
        insight=insight,
        evolution=evolution,
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
    if not _cluster_in_mp_scope(cluster):
        flash("This demand is outside your constituency.", "error")
        return redirect(url_for("government.dashboard"))
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
            decided_by_account_id=current_actor_id(),
            decided_by_role=role,
            decision_type=decision_type,
            reason=reason,          # human-authored — sourced from form only
            linked_project_id=linked_project_id,
        )
        db.session.add(decision)
        db.session.flush()
        log_action("government", current_actor_id(), "decision_recorded", "demand_cluster", cluster_id,
                   after_state={"decision_type": decision_type})

        # Update cluster workflow state
        cluster.review_status = "Decided"
        if decision_type == "Prioritize":
            cluster.active_status = "UnderGovernmentReview"
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

        db.session.commit()
        flash("Decision recorded.", "success")
        return redirect(url_for("government.evidence_detail",
                                cluster_id=cluster_id))

    # GET — collect available projects for the "Link project" dropdown
    projects = Project.query.filter_by(country_id=cluster.country_id).all()
    category = db.session.get(Category, cluster.category_id)
    gap = gap_assessment.calculate(cluster_id)

    return render_template(
        "government/decision_workspace.html",
        cluster=cluster,
        category=category,
        role=role,
        existing_decision=existing_decision,
        projects=projects,
        gap=gap,
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
@require_role("mp", "planning_officer")
def propose_project(cluster_id):
    """
    Create a new Project linked to this DemandCluster (Government §3 —
    Planning Officer: "Propose an action/project candidate"). If a
    GovernmentDecision already exists for this cluster, link it to the new
    project so the citizen timeline picks it up immediately.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
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

    log_action("government", current_actor_id(), "project_proposed", "project", project.id,
               after_state={"name": name, "demand_cluster_id": cluster_id})
    db.session.commit()
    flash(f'Project "{name}" created and linked to this demand.', "success")
    return redirect(url_for("government.evidence_detail", cluster_id=cluster_id))


@government_bp.route("/projects/<project_id>/update-status", methods=["POST"])
@require_role("mp", "planning_officer")
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

    log_action("government", current_actor_id(), "project_status_updated", "project", project.id,
               after_state={"status": new_status})
    db.session.commit()
    flash("Project status updated.", "success")
    return redirect(url_for("government.projects_outcomes"))


@government_bp.route("/projects/<project_id>/outcome", methods=["POST"])
@require_role("mp", "planning_officer")
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

    db.session.flush()
    log_action("government", current_actor_id(), "outcome_recorded", "outcome", outcome.id,
               after_state={"status": outcome.status})
    db.session.commit()
    flash("Outcome recorded.", "success")
    return redirect(url_for("government.projects_outcomes"))


# ---------------------------------------------------------------------------
# Screen 5 — Projects / Outcomes
# ---------------------------------------------------------------------------

@government_bp.route("/projects")
@require_role("mp", "planning_officer", "reviewer")
def projects_outcomes():
    """Linked interventions and progress/outcome status."""
    country_code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=country_code).first()
    country_id = country.id if country else None

    # MP-scoped to their own constituency's projects, same rule as every
    # other government list view — this page previously showed every
    # project in the country to every MP regardless of region_id.
    _, mp_region_ids, _ = _government_scope(country_id)

    project_query = Project.query.filter_by(country_id=country_id)
    if mp_region_ids:
        project_query = project_query.filter(or_(Project.region_id.in_(mp_region_ids), Project.region_id.is_(None)))
    projects = project_query.order_by(Project.updated_at.desc()).all()

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

    # Summary strip — Round 4 UI polish, same project_data pool, just tallied.
    project_stats = {
        "total": len(project_data),
        "in_progress": len([d for d in project_data if d["project"].status not in ("Completion",)]),
        "completed": len([d for d in project_data if d["project"].status == "Completion"]),
        "verified_outcomes": len([d for d in project_data if d["outcome"] and d["outcome"].status == "Verified"]),
    }

    return render_template(
        "government/projects_outcomes.html",
        project_data=project_data,
        project_stats=project_stats,
        role=current_role(),
    )


# ---------------------------------------------------------------------------
# Screen 6 — Admin
# ---------------------------------------------------------------------------

@government_bp.route("/admin", methods=["GET", "POST"])
@require_role("admin")
def admin():
    """
    Technical configuration only — no policy authority (Progress Log §5.1).
    Also the only place government accounts get provisioned — there is no
    self-registration for any government role (national_admin, state_admin,
    district_officer, department_officer, analyst, reviewer). The DB has no
    invite-token table for these accounts (see app/models/auth_models.py
    provenance note), so provisioning creates the account directly with a
    generated one-time password (see provision_government_user() below).
    """
    countries = Country.query.all()
    categories = Category.query.all()
    departments = Department.query.order_by(Department.name).all()

    from app.auth.session import get_all_demo_actors
    actors = get_all_demo_actors()

    gov_accounts = (
        GovernmentAccount.query
        .filter_by(is_demo=False)
        .order_by(GovernmentAccount.created_at.desc())
        .all()
    )

    return render_template(
        "government/admin.html",
        countries=countries,
        categories=categories,
        departments=departments,
        actors=actors,
        gov_accounts=gov_accounts,
    )


@government_bp.route("/admin/provision", methods=["POST"])
@require_role("admin")
def provision_government_user():
    """
    Directly create a new government account (no invite-token flow exists
    for these roles — see admin() docstring). A random one-time password is
    generated and either emailed to the new account or, if SMTP isn't
    configured, shown once to the admin performing this action so the flow
    still works without email credentials configured. The new account
    should change this password after first sign-in (see /account).
    """
    from app.models.auth_models import GOVERNMENT_ROLES
    from app.auth.security import is_valid_email, generate_raw_token, hash_password
    from app.services.job_queue import queue_email

    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    role = request.form.get("role")
    department_id = request.form.get("department_id") or None
    country_code = session.get("country_code", "IN")

    country = Country.query.filter_by(code=country_code).first()

    if not name or not is_valid_email(email) or role not in GOVERNMENT_ROLES or country is None:
        flash("Please provide a valid name, email, and role.", "error")
        return redirect(url_for("government.admin"))

    from app.auth.routes import _email_in_use  # single source of truth for cross-table uniqueness
    if _email_in_use(email):
        flash("An account with this email already exists.", "error")
        return redirect(url_for("government.admin"))

    temp_password = generate_raw_token()[:16]
    account = GovernmentAccount(
        email=email,
        password_hash=hash_password(temp_password),
        full_name=name,
        role=role,
        country_id=country.id,
        department_id=department_id,
        provisioned_by=current_actor_id(),
        is_active=True,
        is_demo=False,
    )
    db.session.add(account)
    db.session.flush()
    log_action("government", current_actor_id(), "government_account_provisioned",
               "government_account", account.id, ip_address=(request.remote_addr or "")[:64])
    db.session.commit()

    signin_url = url_for("auth.signin", _external=True)
    sent = queue_email(
        email,
        "Your BRICS People First government account",
        f"Hello {name},\n\n"
        f"An account has been created for you as {role.replace('_', ' ').title()}.\n"
        f"Sign in at {signin_url} with:\n  Email: {email}\n  Temporary password: {temp_password}\n\n"
        "Please change this password after signing in (Account page).",
    )
    db.session.commit()  # persist the queued job row (if any)

    if sent:
        flash(f"Account created for {email} — credentials emailed.", "success")
    else:
        flash(
            f"Email is not configured on this server — share these credentials with {name} directly: "
            f"{email} / {temp_password}",
            "info",
        )
    return redirect(url_for("government.admin"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_cluster_card(c: DemandCluster) -> dict:
    """
    Shared cluster-summary card builder — the shape _cluster_card.html
    expects. Factored out of dashboard() so demand_intelligence() (and any
    future page) computes the exact same evidence/alignment/priority for a
    cluster rather than each route re-deriving it slightly differently.
    Same services evidence_detail() uses (Government §5.3.4).
    """
    cat = db.session.get(Category, c.category_id)
    sev = _dominant_severity(c.id)

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
    pending = c.review_status in ("UnderReview", "PendingValidation")
    return {
        "cluster": c,
        "category_name": cat.name if cat else "",
        "category_code": cat.code if cat else "",
        "total_reports": c.total_reports,
        "unique_contributors": c.unique_contributors,
        "sentiment": c.community_sentiment,
        "dominant_severity": sev,
        "pending_decision": pending,
        "priority": s2.priority,
        "confidence": s1.confidence,
        "alignment_state": alignment.state,
    }


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
