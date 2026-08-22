# citizen/routes.py
#
# All citizen-facing routes — Screens 1–6 per BRICS_CITIZEN_MVP_FEATURES §17.2.
#
# Hard rules:
#   - Never import from government/ — cross-side reads go through models/ directly.
#   - AI calls live here only, never in templates.
#   - original_raw_input is never written to after Report creation.
#   - Draft rows are excluded from /community, /map, and contributor counts.

from flask import (
    render_template, request, session, redirect, url_for, flash, jsonify
)
from geoalchemy2.functions import ST_AsGeoJSON
from sqlalchemy import func
import json

from app.citizen import citizen_bp
from app.extensions import db
from app.models.citizen_models import Report, Contribution, Verification, Evidence
from app.models.demand_cluster import DemandCluster
from app.models.shared import Category, AdministrativeRegion, EventLog
# Cross-side reads — government data read directly from models/, no government/ import
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.auth.session import current_actor_id, current_role


# ---------------------------------------------------------------------------
# Screen 1 — Home
# ---------------------------------------------------------------------------

@citizen_bp.route("/")
def home():
    """Single primary CTA — tell us what your community needs."""
    return render_template("citizen/home.html")


# ---------------------------------------------------------------------------
# Screen 2 — Report Flow
# ---------------------------------------------------------------------------

@citizen_bp.route("/report", methods=["GET", "POST"])
def report_flow():
    """
    GET  — show the input form (single text area + optional voice).
    POST — run the full pipeline:
             voice  → elevenlabs_client.transcribe()
             text   → groq_client.extract_report_fields()
             gate   → Category + Location → Draft or Report
             Report → cohere_client.embed() → demand_matching.find_similar()
    """
    if request.method == "GET":
        return render_template("citizen/report_flow.html")

    # --- Determine channel and get raw text ---
    channel = request.form.get("channel", "text")
    raw_text = ""
    location_hint = (request.form.get("location_hint") or "").strip()

    if channel == "voice":
        audio = request.files.get("audio")
        if not audio:
            flash("No audio received. Please try again.", "error")
            return render_template("citizen/report_flow.html")
        try:
            from app.services.elevenlabs_client import transcribe_audio, TranscriptionError
            result = transcribe_audio(audio.read(), mime_type=audio.mimetype or "audio/webm")
            raw_text = result["text"]
        except TranscriptionError as e:
            flash(str(e), "error")
            return render_template("citizen/report_flow.html")
    else:
        raw_text = (request.form.get("text_input") or "").strip()
        if not raw_text:
            flash("Please describe your community's need.", "error")
            return render_template("citizen/report_flow.html")

    # --- AI extraction ---
    # If citizen provided a location hint, append it to the text so Groq
    # can extract it as the location field. Keeps the pipeline unchanged.
    extraction_text = raw_text
    if location_hint:
        extraction_text = raw_text + f"\n[Location hint: {location_hint}]"

    from app.services.groq_client import extract_report_fields, ask_clarification
    extracted = extract_report_fields(extraction_text)
    meta = extracted.get("meta", {})

    # Resolve category_id from category code
    category_id = None
    if extracted.get("category"):
        cat = Category.query.filter_by(code=extracted["category"]).first()
        if cat:
            category_id = cat.id

    # Resolve region_id from location string (best-effort fuzzy match on name)
    region_id = None
    country_id = _country_id_from_session()
    if extracted.get("location") and country_id:
        region = (
            AdministrativeRegion.query
            .filter_by(country_id=country_id)
            .filter(AdministrativeRegion.name.ilike(f"%{extracted['location']}%"))
            .first()
        )
        if region:
            region_id = region.id

    # --- Draft/Report gate (Progress Log §13.1) ---
    status = "Unclustered" if meta.get("complete") else "Draft"

    report = Report(
        citizen_id=current_actor_id() or "anon",
        country_id=country_id or "country-in",
        region_id=region_id,
        category_id=category_id,
        original_raw_input=raw_text,   # write-once — never updated after this line
        original_language=extracted.get("language_detected"),
        channel=channel,
        severity=extracted.get("severity"),
        duration=extracted.get("duration"),
        affected_group=extracted.get("affected_group"),
        status=status,
    )
    db.session.add(report)
    db.session.flush()   # get report.id before commit

    # EventLog: Submitted
    db.session.add(EventLog(
        report_id=report.id,
        stage="Submitted",
    ))

    # If AI understood it (even Draft), log AIUnderstood
    db.session.add(EventLog(
        report_id=report.id,
        stage="AIUnderstood",
        metadata_={"summary": extracted.get("problem_summary", "")},
    ))

    if status == "Draft":
        # Not enough info yet — ask one clarification question
        clarification = ask_clarification(raw_text, meta.get("missing_fields", []))
        db.session.commit()
        return render_template(
            "citizen/report_flow.html",
            clarification=clarification,
            report_id=report.id,
            partial_text=raw_text,
            location_hint=location_hint,
        )

    # --- Report is complete: embed + match ---
    from app.services.cohere_client import embed_text
    from app.services.demand_matching import find_similar_clusters

    embedding = embed_text(extracted.get("problem_summary") or raw_text)

    match_result = None
    if category_id and country_id:
        match_result = find_similar_clusters(
            report_text=extracted.get("problem_summary") or raw_text,
            category_id=category_id,
            country_id=country_id,
        )

    db.session.commit()
    return redirect(url_for("citizen.demand_result", report_id=report.id))


# ---------------------------------------------------------------------------
# Screen 3 — Demand Result
# ---------------------------------------------------------------------------

@citizen_bp.route("/report/<report_id>/demand", methods=["GET", "POST"])
def demand_result(report_id):
    """
    GET  — show match results: auto-suggest join / show candidates / start new.
    POST — citizen action: join, confirm, add_evidence, or start_new.
           Writes a Contribution row.  Never touches report.original_raw_input.
    """
    report = Report.query.get_or_404(report_id)

    if request.method == "POST":
        action = request.form.get("action")
        cluster_id = request.form.get("cluster_id")

        if action == "start_new":
            # Citizen explicitly wants a new cluster — create one
            cluster = _create_cluster_from_report(report)
            _add_contribution(report, cluster.id, "joined")
            db.session.commit()
            flash("Your community issue has been started.", "success")
            return redirect(url_for("citizen.community"))

        if action in ("join", "confirm", "add_evidence") and cluster_id:
            contrib_type = "joined" if action == "join" else \
                           "confirmed" if action == "confirm" else "evidence_added"
            _add_contribution(report, cluster_id, contrib_type)
            db.session.commit()
            flash("You've joined this community issue.", "success")
            return redirect(url_for("citizen.community"))

        flash("Unknown action.", "error")

    # GET — compute match result for display
    from app.services.demand_matching import find_similar_clusters, MatchResult
    match_result = None
    if report.category_id and report.country_id:
        match_result = find_similar_clusters(
            report_text=report.original_raw_input,
            category_id=report.category_id,
            country_id=report.country_id,
        )

    # Enrich matches with cluster objects for display
    clusters = []
    if match_result and match_result.matches:
        for m in match_result.matches:
            c = db.session.get(DemandCluster, m.cluster_id)
            if c:
                clusters.append({
                    "cluster": c,
                    "similarity": round(m.similarity * 100),
                    "total_reports": c.total_reports,
                    "unique_contributors": c.unique_contributors,
                })

    return render_template(
        "citizen/demand_result.html",
        report=report,
        match_tier=match_result.tier if match_result else "no_match",
        clusters=clusters,
    )


# ---------------------------------------------------------------------------
# Screen 4 — Community Demand
# ---------------------------------------------------------------------------

@citizen_bp.route("/community")
def community():
    """
    Aggregated demand view — only clusters with at least one Report (not Draft).
    Filterable by category and region.
    """
    category_filter = request.args.get("category")
    region_filter = request.args.get("region")

    query = DemandCluster.query.filter(
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"])
    )
    if category_filter:
        query = query.filter_by(category_id=category_filter)

    clusters = query.order_by(DemandCluster.created_at.desc()).limit(50).all()

    # Annotate each cluster with derived counts (avoids N+1 in template)
    cluster_data = []
    for c in clusters:
        cat = db.session.get(Category, c.category_id)
        cluster_data.append({
            "cluster": c,
            "category_name": cat.name if cat else "",
            "total_reports": c.total_reports,
            "unique_contributors": c.unique_contributors,
            "sentiment": c.community_sentiment,
        })

    categories = Category.query.all()

    return render_template(
        "citizen/community_demand.html",
        cluster_data=cluster_data,
        categories=categories,
        active_category=category_filter,
    )


# ---------------------------------------------------------------------------
# Screen 5 — Demand Map
# ---------------------------------------------------------------------------

@citizen_bp.route("/map")
def demand_map():
    """
    PostGIS-aggregated hotspots for the community demand map.
    Returns a page that fetches /citizen/map/data as JSON for the map JS.
    """
    categories = Category.query.all()
    return render_template("citizen/demand_map.html", categories=categories)


@citizen_bp.route("/map/data")
def demand_map_data():
    """
    GeoJSON endpoint consumed by the map JS.
    Returns only clusters with a centroid and at least Active status.
    """
    category_filter = request.args.get("category")

    query = db.session.query(
        DemandCluster.id,
        DemandCluster.category_id,
        DemandCluster.active_status,
        DemandCluster.affected_localities,
        ST_AsGeoJSON(DemandCluster.centroid).label("geojson"),
    ).filter(
        DemandCluster.centroid.isnot(None),
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
    )

    if category_filter:
        query = query.filter(DemandCluster.category_id == category_filter)

    rows = query.all()

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
                "category": cat.name if cat else "",
                "status": row.active_status,
                "localities": row.affected_localities or [],
            },
        })

    return jsonify({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# Screen 6 — My Timeline
# ---------------------------------------------------------------------------

@citizen_bp.route("/timeline")
def my_timeline():
    """
    Citizen's personal journey: Report → Demand → Government → Outcome.
    Requires a citizen session.
    """
    if current_role() != "citizen" or not current_actor_id():
        flash("Select a citizen account to view your timeline.", "info")
        return redirect(url_for("role_select"))

    actor_id = current_actor_id()

    # All reports by this citizen
    reports = (
        Report.query
        .filter_by(citizen_id=actor_id)
        .order_by(Report.created_at.desc())
        .all()
    )

    timeline_items = []
    for report in reports:
        # Get the cluster this report joined (if any)
        contrib = Contribution.query.filter_by(
            report_id=report.id, citizen_id=actor_id
        ).first()

        cluster = None
        decision = None
        project = None
        outcome = None

        if contrib:
            cluster = db.session.get(DemandCluster, contrib.demand_cluster_id)
            if cluster:
                # Government decision — read directly from models/, never from government/
                decision = (
                    GovernmentDecision.query
                    .filter_by(demand_cluster_id=cluster.id)
                    .order_by(GovernmentDecision.timestamp.desc())
                    .first()
                )
                if decision and decision.linked_project_id:
                    project = db.session.get(Project, decision.linked_project_id)
                    if project:
                        outcome = Outcome.query.filter_by(
                            project_id=project.id
                        ).first()

        # EventLog for this report
        events = (
            EventLog.query
            .filter_by(report_id=report.id)
            .order_by(EventLog.timestamp.asc())
            .all()
        )

        timeline_items.append({
            "report": report,
            "cluster": cluster,
            "decision": decision,
            "project": project,
            "outcome": outcome,
            "events": events,
        })

    return render_template(
        "citizen/my_timeline.html",
        timeline_items=timeline_items,
    )


# ---------------------------------------------------------------------------
# Community Verification (inline action from /community)
# ---------------------------------------------------------------------------

@citizen_bp.route("/cluster/<cluster_id>/verify", methods=["POST"])
def verify_cluster(cluster_id):
    """
    Record a Verification vote (StillHappening / Improved / Worse / Resolved).
    NEVER writes to DemandCluster.active_status — display-only sentiment (§13.2).
    """
    state = request.form.get("state")
    valid_states = ("StillHappening", "Improved", "Worse", "Resolved")
    if state not in valid_states:
        flash("Invalid verification state.", "error")
        return redirect(url_for("citizen.community"))

    actor_id = current_actor_id() or "anon"
    db.session.add(Verification(
        citizen_id=actor_id,
        demand_cluster_id=cluster_id,
        state=state,
    ))
    db.session.commit()
    flash("Thanks for the update.", "success")
    return redirect(url_for("citizen.community"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _country_id_from_session() -> str:
    """Map session country_code to a country ID. Falls back to India."""
    from app.models.shared import Country
    code = session.get("country_code", "IN")
    country = Country.query.filter_by(code=code).first()
    return country.id if country else "country-in"


def _create_cluster_from_report(report: Report) -> DemandCluster:
    """
    Create a new DemandCluster seeded from a completed Report.
    Only called when citizen explicitly chooses 'Start new issue' —
    never called silently (Progress Log §5.2.3 anti-manipulation rule).
    """
    cluster = DemandCluster(
        country_id=report.country_id,
        region_ids=[report.region_id] if report.region_id else [],
        category_id=report.category_id,
        affected_localities=[],
        trend="stable",
        confidence="low",
        active_status="Active",
        review_status="NotReviewed",
    )
    db.session.add(cluster)
    db.session.flush()

    # Kick off background-free embedding (synchronous for MVP)
    try:
        from app.services.demand_matching import store_cluster_embedding
        store_cluster_embedding(cluster.id, report.original_raw_input)
    except Exception:
        pass  # embedding failure doesn't block cluster creation for MVP

    return cluster


def _add_contribution(report: Report, cluster_id: str, contrib_type: str):
    """Add a Contribution and update the Report status to Clustered."""
    contrib = Contribution(
        report_id=report.id,
        citizen_id=report.citizen_id,
        demand_cluster_id=cluster_id,
        type=contrib_type,
    )
    db.session.add(contrib)
    report.status = "Clustered"

    db.session.add(EventLog(
        report_id=report.id,
        demand_cluster_id=cluster_id,
        stage="JoinedDemand",
    ))
