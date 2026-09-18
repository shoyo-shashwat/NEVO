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
from sqlalchemy import func, case
from datetime import datetime, timezone
import json
import logging
import time

logger = logging.getLogger(__name__)

from app.citizen import citizen_bp
from app.extensions import db
from app.models.citizen_models import Report, Contribution, Verification, Evidence
from app.models.demand_cluster import DemandCluster
from app.models.shared import Category, AdministrativeRegion, EventLog
# Cross-side reads — government data read directly from models/, no government/ import
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.auth.session import current_actor_id, current_role, current_user, current_account_type


# ---------------------------------------------------------------------------
# Screen 1 — Home
# ---------------------------------------------------------------------------

@citizen_bp.route("/")
def home():
    """
    Home answers "what is happening in my area, and how can I participate?"
    (India-only MVP design review) — not just the report CTA. Community
    snapshot + top demands are real counts scoped to the citizen's country;
    personal impact summary only for a logged-in citizen with real
    contributions (never shown empty/fabricated for anonymous visitors).
    """
    country_id = _country_id_from_session()

    clusters = (
        DemandCluster.query
        .filter(
            DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
            DemandCluster.country_id == country_id,
        )
        .all()
    )

    snapshot = {
        "participants": sum(c.unique_contributors for c in clusters),
        "active_demands": len(clusters),
        "projects_tracked": Project.query.filter_by(country_id=country_id).count() if country_id else 0,
        "projects_completed": Project.query.filter_by(country_id=country_id, status="Completion").count() if country_id else 0,
    }

    top_demands = sorted(clusters, key=lambda c: c.unique_contributors, reverse=True)[:3]
    top_demand_cards = []
    for c in top_demands:
        cat = db.session.get(Category, c.category_id)
        localities = c.affected_localities or []
        top_demand_cards.append({
            "cluster": c,
            "category_name": cat.name if cat else "",
            "category_code": cat.code if cat else "",
            "unique_contributors": c.unique_contributors,
            "total_reports": c.total_reports,
            "trend": c.trend,
            "active_status": c.active_status,
            "locality": localities[0] if localities else "",
        })

    personal = None
    if current_role() == "citizen" and current_actor_id():
        actor_id = current_actor_id()
        reports_count = Report.query.filter_by(citizen_account_id=actor_id).count()
        if reports_count:
            joined_count = Contribution.query.filter_by(citizen_account_id=actor_id).count()
            resolved_count = (
                db.session.query(func.count(func.distinct(Contribution.demand_cluster_id)))
                .join(Outcome, Outcome.demand_cluster_id == Contribution.demand_cluster_id)
                .filter(Contribution.citizen_account_id == actor_id, Outcome.status == "Verified")
                .scalar() or 0
            )
            personal = {
                "reports_submitted": reports_count,
                "demands_joined": joined_count,
                "issues_resolved": resolved_count,
            }

    return render_template(
        "citizen/home.html",
        snapshot=snapshot,
        top_demand_cards=top_demand_cards,
        personal=personal,
    )


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

    # GPS — optional, client-supplied via navigator.geolocation (report_flow.html)
    lat_raw = request.form.get("latitude", "").strip()
    lng_raw = request.form.get("longitude", "").strip()
    latitude = float(lat_raw) if lat_raw else None
    longitude = float(lng_raw) if lng_raw else None

    # AI calls in this view are buffered here and persisted as
    # AIProcessingLog rows once report.id exists (after the flush below) —
    # the audit trail the AI pipeline requires: provider/model/confidence/
    # timestamp/structured-output for every call, without ever touching
    # Report.original_raw_input itself.
    ai_log_entries = []

    if channel == "voice":
        audio = request.files.get("audio")
        if not audio:
            flash("No audio received. Please try again.", "error")
            return render_template("citizen/report_flow.html")
        try:
            from app.services.elevenlabs_client import transcribe_audio, TranscriptionError
            _t0 = time.monotonic()
            result = transcribe_audio(audio.read(), mime_type=audio.mimetype or "audio/webm")
            raw_text = result["text"]
            ai_log_entries.append(dict(
                stage="transcription", provider="elevenlabs", model="scribe_v2",
                success=True, latency_ms=int((time.monotonic() - _t0) * 1000),
                structured_output={"language_detected": result.get("language_detected"),
                                    "duration_seconds": result.get("duration_seconds")},
            ))
        except TranscriptionError as e:
            # No Report will ever exist for this attempt (channel-level
            # failure — see elevenlabs_client.py) — log immediately with
            # report_id=None rather than buffering to a report that won't
            # be created.
            from app.models.ai_models import log_ai_call
            log_ai_call("transcription", "elevenlabs", "scribe_v2",
                        success=False, error_message=str(e)[:2000])
            db.session.commit()
            flash(str(e), "error")
            return render_template("citizen/report_flow.html")
    else:
        raw_text = (request.form.get("text_input") or "").strip()
        if not raw_text:
            flash("Please describe your community's need.", "error")
            return render_template("citizen/report_flow.html")

    # --- Ingestion & Safety: rate limit + duplicate guard ---
    # Docs (Government/Citizen architecture, Layer 2 "Ingestion & Safety" /
    # "Trust & Authenticity Layer") call for rate limiting and duplicate
    # detection before a submission is treated as a new, distinct voice.
    # Kept intentionally lightweight for an MVP — this is not a full abuse
    # pipeline, just enough that one person can't inflate demand by
    # double-submitting, and that a slip of the submit button doesn't create
    # noise. Never blocks a genuinely new report.
    guard_redirect = _guard_against_spam_and_duplicates(raw_text)
    if guard_redirect:
        return guard_redirect

    # --- AI extraction ---
    # If citizen provided a location hint, append it to the text so Groq
    # can extract it as the location field. Keeps the pipeline unchanged.
    extraction_text = raw_text
    if location_hint:
        extraction_text = raw_text + f"\n[Location hint: {location_hint}]"

    from app.services.groq_client import extract_report_fields, ask_clarification
    _t0 = time.monotonic()
    try:
        extracted = extract_report_fields(extraction_text)
        ai_log_entries.append(dict(
            stage="extraction", provider="groq", model=_groq_model_name(),
            success=True, latency_ms=int((time.monotonic() - _t0) * 1000),
            structured_output={k: v for k, v in extracted.items() if k != "meta"},
        ))
    except Exception as e:
        # Groq API error, truncated response, or JSON parse failure.
        # Never 500 during a live demo — fall back to Draft with a retry message.
        logger.warning("Groq extraction failed (%s), falling back to Draft: %s",
                       type(e).__name__, str(e)[:120])
        ai_log_entries.append(dict(
            stage="extraction", provider="groq", model=_groq_model_name(),
            success=False, latency_ms=int((time.monotonic() - _t0) * 1000),
            error_message=f"{type(e).__name__}: {str(e)[:500]}",
        ))
        extracted = {
            "category": None, "location": None, "severity": None,
            "duration": None, "affected_group": None,
            "problem_summary": None, "language_detected": None,
            "meta": {"complete": False, "missing_fields": ["category", "location"]},
        }
    meta = extracted.get("meta", {})

    # Resolve category_id from category code
    category_id = None
    if extracted.get("category"):
        cat = Category.query.filter_by(code=extracted["category"]).first()
        if cat:
            category_id = cat.id

    # Resolve region_id from location string (best-effort fuzzy match on name).
    # Finding 3 (Citizen Report Flow Audit): matches against district/city-level
    # rows seeded in seed/seed_data.py::seed_district_regions(), not just the
    # two state-level rows per country. If nothing matches at all, degrade
    # gracefully to the country's broadest (state-level) region rather than
    # silently leaving region_id null with no location scoping whatsoever —
    # Option B fallback from the audit, layered on top of the Option A seed fix.
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
        else:
            # No specific match — fall back to the broadest seeded region for
            # this country so the report still carries some location scoping.
            fallback_region = (
                AdministrativeRegion.query
                .filter_by(country_id=country_id, level="state_province")
                .order_by(AdministrativeRegion.name.asc())
                .first()
            )
            if fallback_region:
                region_id = fallback_region.id
                logger.info(
                    "No region match for location hint %r — falling back to %s",
                    extracted.get("location"), fallback_region.name,
                )

    # --- Draft/Report gate (Progress Log §13.1) ---
    status = "Unclustered" if meta.get("complete") else "Draft"

    citizen_account_id, anonymous_token = _current_identity()
    report = Report(
        citizen_account_id=citizen_account_id,
        anonymous_token=anonymous_token,
        consent_given_at=datetime.now(timezone.utc),
        country_id=country_id or "country-in",
        region_id=region_id,
        category_id=category_id,
        original_raw_input=raw_text,   # write-once — never updated after this line
        original_language=extracted.get("language_detected"),
        problem_summary_en=extracted.get("problem_summary_en"),
        channel=channel,
        severity=extracted.get("severity"),
        duration=extracted.get("duration"),
        affected_group=extracted.get("affected_group"),
        status=status,
        latitude=latitude,
        longitude=longitude,
    )
    db.session.add(report)
    db.session.flush()   # get report.id before commit

    # Persist the buffered AI audit entries now that report.id exists.
    from app.models.ai_models import log_ai_call
    for entry in ai_log_entries:
        log_ai_call(report_id=report.id, **entry)

    # Optional evidence photo (Finding 6 — Citizen Report Flow Audit).
    # Stored locally under app/static/uploads/evidence/ (see services/evidence_storage.py).
    # Failure here must never block report submission — evidence is optional.
    evidence_file = request.files.get("evidence_photo")
    if evidence_file and evidence_file.filename:
        try:
            from app.services.evidence_storage import save_evidence_photo, EvidenceUploadError
            photo_url = save_evidence_photo(evidence_file)
            db.session.add(Evidence(
                type="photo",
                url=photo_url,
                uploaded_by=current_actor_id() or "anon",
                attached_to="Report",
                attached_to_id=report.id,
                report_id=report.id,
            ))
        except EvidenceUploadError as e:
            logger.warning("Evidence photo upload skipped: %s", e)
        except Exception as e:
            logger.warning("Evidence photo upload failed, continuing without it: %s", e)

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
        # Not enough info yet — ask one clarification question.
        # If extraction itself failed, show a generic retry prompt.
        if not extracted.get("meta", {}).get("missing_fields"):
            clarification = "Could you describe the problem in a bit more detail?"
        else:
            _t0 = time.monotonic()
            try:
                clarification = ask_clarification(raw_text, extracted["meta"]["missing_fields"])
                log_ai_call(report_id=report.id, stage="clarification", provider="groq",
                            model=_groq_model_name(), success=True,
                            latency_ms=int((time.monotonic() - _t0) * 1000),
                            structured_output={"missing_fields": extracted["meta"]["missing_fields"]})
            except Exception as e:
                log_ai_call(report_id=report.id, stage="clarification", provider="groq",
                            model=_groq_model_name(), success=False,
                            latency_ms=int((time.monotonic() - _t0) * 1000),
                            error_message=f"{type(e).__name__}: {str(e)[:500]}")
                clarification = "Could you describe the problem in a bit more detail?"
        db.session.commit()
        return render_template(
            "citizen/report_flow.html",
            clarification=clarification,
            report_id=report.id,
            partial_text=raw_text,
            location_hint=location_hint,
            latitude=latitude,
            longitude=longitude,
        )

    # --- Report is complete: match against existing demand ---
    # find_similar_clusters() embeds report_text internally (asymmetric
    # "search_query" embedding) — an earlier version of this code also
    # called embed_text() separately just before this with its result
    # discarded, paying for a second Cohere call with no effect. Removed;
    # the one embedding call below is the only one that actually matters.
    from app.services.demand_matching import find_similar_clusters

    _t0 = time.monotonic()
    try:
        match_result = None
        if category_id and country_id:
            match_result = find_similar_clusters(
                report_text=extracted.get("problem_summary") or raw_text,
                category_id=category_id,
                country_id=country_id,
            )
        top_similarity = match_result.matches[0].similarity if match_result and match_result.matches else None
        log_ai_call(report_id=report.id, stage="demand_matching", provider="cohere", model="embed-v4.0",
                    success=True, latency_ms=int((time.monotonic() - _t0) * 1000),
                    confidence=(f"{top_similarity:.3f}" if top_similarity is not None else None),
                    structured_output={"tier": match_result.tier if match_result else "no_match",
                                        "candidate_count": len(match_result.matches) if match_result else 0})
    except Exception as embed_err:
        # Embedding or pgvector query failed — rollback and continue without matching.
        # Report is still saved as Unclustered; citizen can still see the demand result page.
        logger.warning("Embed/match failed, continuing without cluster match: %s", embed_err)
        db.session.rollback()
        # Re-add the report after rollback
        db.session.add(report)
        log_ai_call(report_id=report.id, stage="demand_matching", provider="cohere", model="embed-v4.0",
                    success=False, latency_ms=int((time.monotonic() - _t0) * 1000),
                    error_message=f"{type(embed_err).__name__}: {str(embed_err)[:500]}")
        match_result = None

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
            # Citizen Contribution Feedback (docs §"Citizen Contribution
            # Feedback") — say what the action actually did, not just that
            # it succeeded.
            flash(
                "Your community issue has been started — you're the first "
                "voice on it. Share it with your neighbours so others can join.",
                "success",
            )
            return redirect(url_for("citizen.community"))

        if action == "add_evidence" and cluster_id:
            # Citizen MVP §5/§6 — "Add Evidence" to an existing DemandCluster,
            # distinct from the report-submission-time evidence in report_flow().
            # A photo is required for this specific action; without one there's
            # nothing to attach, so we don't silently create an empty evidence
            # bookkeeping row.
            evidence_file = request.files.get("evidence_photo")
            if not evidence_file or not evidence_file.filename:
                flash("Attach a photo to add evidence, or use Join instead.", "error")
                return redirect(url_for("citizen.demand_result", report_id=report.id))
            try:
                from app.services.evidence_storage import save_evidence_photo, EvidenceUploadError
                photo_url = save_evidence_photo(evidence_file)
                db.session.add(Evidence(
                    type="photo",
                    url=photo_url,
                    uploaded_by=report.citizen_account_id or report.anonymous_token or "anon",
                    attached_to="DemandCluster",
                    attached_to_id=cluster_id,
                    report_id=report.id,
                ))
            except EvidenceUploadError as e:
                flash(str(e), "error")
                return redirect(url_for("citizen.demand_result", report_id=report.id))
            _add_contribution(report, cluster_id, "evidence_added")
            db.session.commit()
            evidence_count = Evidence.query.filter_by(
                attached_to="DemandCluster", attached_to_id=cluster_id
            ).count()
            flash(
                f"Evidence added — this issue now has {evidence_count} "
                f"piece{'s' if evidence_count != 1 else ''} of supporting evidence.",
                "success",
            )
            return redirect(url_for("citizen.community"))

        if action in ("join", "confirm") and cluster_id:
            contrib_type = "joined" if action == "join" else "confirmed"
            _add_contribution(report, cluster_id, contrib_type)
            db.session.commit()
            cluster = db.session.get(DemandCluster, cluster_id)
            contributors = cluster.unique_contributors if cluster else None
            verb = "joined" if action == "join" else "confirmed"
            if contributors:
                flash(
                    f"You {verb} this community issue — you're now one of "
                    f"{contributors} people speaking up about it.",
                    "success",
                )
            else:
                flash(f"You've {verb} this community issue.", "success")
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
                cat = db.session.get(Category, c.category_id)
                clusters.append({
                    "cluster": c,
                    "category_name": cat.name if cat else "",
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

    citizen_account_id, anonymous_token = _current_identity()

    # Annotate each cluster with derived counts (avoids N+1 in template)
    cluster_data = []
    for c in clusters:
        cat = db.session.get(Category, c.category_id)
        supported_query = Contribution.query.filter_by(demand_cluster_id=c.id)
        if citizen_account_id:
            supported_query = supported_query.filter_by(citizen_account_id=citizen_account_id)
        else:
            supported_query = supported_query.filter_by(anonymous_token=anonymous_token)
        cluster_data.append({
            "cluster": c,
            "category_name": cat.name if cat else "",
            "category_code": cat.code if cat else "",
            "total_reports": c.total_reports,
            "unique_contributors": c.unique_contributors,
            "sentiment": c.community_sentiment,
            "already_supported": supported_query.first() is not None,
        })

    categories = Category.query.all()

    # Community-wide activity snapshot — always unfiltered by category, so the
    # panel reads as "this community" context rather than shifting with filters.
    all_active = DemandCluster.query.filter(
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"])
    ).all()
    areas = {loc for c in all_active for loc in (c.affected_localities or [])}
    community_stats = {
        "total_participants": sum(c.unique_contributors for c in all_active),
        "active_demands": len(all_active),
        "areas_covered": len(areas),
    }

    return render_template(
        "citizen/community_demand.html",
        cluster_data=cluster_data,
        categories=categories,
        active_category=category_filter,
        community_stats=community_stats,
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
    from app.services.map_view import get_map_view

    categories = Category.query.all()
    country_code = session.get("country_code", "IN")
    return render_template(
        "citizen/demand_map.html",
        categories=categories,
        country_code=country_code,
        map_view=get_map_view(country_code),
    )


@citizen_bp.route("/map/data")
def demand_map_data():
    """
    GeoJSON endpoint consumed by the map JS.
    Returns only clusters with a centroid and at least Active status,
    scoped to the current session's country (Round 4 fix — this used to
    return every country's clusters on one whole-world view; a citizen or
    government user should only ever see their own country's map).
    """
    category_filter = request.args.get("category")
    country_id = _country_id_from_session()

    query = db.session.query(
        DemandCluster.id,
        DemandCluster.category_id,
        DemandCluster.active_status,
        DemandCluster.affected_localities,
        DemandCluster.trend,
        ST_AsGeoJSON(DemandCluster.centroid).label("geojson"),
    ).filter(
        DemandCluster.centroid.isnot(None),
        DemandCluster.active_status.in_(["Active", "UnderGovernmentReview"]),
        DemandCluster.country_id == country_id,
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
                "category_code": cat.code if cat else "",
                "status": row.active_status,
                "localities": row.affected_localities or [],
                "trend": row.trend,
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
        return redirect(url_for("login_page"))

    actor_id = current_actor_id()

    # All reports by this citizen
    reports = (
        Report.query
        .filter_by(citizen_account_id=actor_id)
        .order_by(Report.created_at.desc())
        .all()
    )

    timeline_items = []
    for report in reports:
        # Get the cluster this report joined (if any)
        contrib = Contribution.query.filter_by(
            report_id=report.id, citizen_account_id=actor_id
        ).first()

        cluster = None
        decision = None
        project = None
        outcome = None
        simplified_reason = None
        report_category_name = None

        # Category UUID → name (same bug class as Finding 1, this template
        # was not covered by that fix). Never show report.category_id raw.
        if report.category_id:
            report_cat = db.session.get(Category, report.category_id)
            report_category_name = report_cat.name if report_cat else None

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
                # Finding 4 — simplify the human-authored reason for citizen
                # display. Never replaces decision.reason itself — only adds a
                # display-only simplified version. Falls back to the original
                # text if the AI call fails, so the citizen always sees
                # something (don't let AI failures break the page).
                if decision and decision.reason:
                    try:
                        from app.services.groq_client import simplify_decision_for_citizen
                        simplified_reason = simplify_decision_for_citizen(
                            decision.reason,
                            language_code=report.original_language or "en",
                        )
                    except Exception as e:
                        logger.warning(
                            "simplify_decision_for_citizen failed, showing original reason: %s", e
                        )
                        simplified_reason = None
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
            "category_name": report_category_name,
            "cluster": cluster,
            "decision": decision,
            "simplified_reason": simplified_reason,
            "project": project,
            "outcome": outcome,
            "events": events,
        })

    # "My Reports" style summary strip (Round 4 UI addition, inspired by a
    # shared reference mockup) — same timeline_items pool, just tallied.
    timeline_stats = {
        "reported": len(timeline_items),
        "joined": len([t for t in timeline_items if t["cluster"]]),
        "decided": len([t for t in timeline_items if t["decision"]]),
        "resolved": len([t for t in timeline_items if t["outcome"] and t["outcome"].status == "Verified"]),
    }

    return render_template(
        "citizen/my_timeline.html",
        timeline_items=timeline_items,
        timeline_stats=timeline_stats,
    )


# ---------------------------------------------------------------------------
# Screen 7 — Impact ("Did anything actually change?")
# ---------------------------------------------------------------------------

@citizen_bp.route("/impact")
def impact():
    """
    Two-part answer to "did anything actually change?":

    1. A country-wide funnel — citizen voices -> collective demands ->
       government decisions -> projects -> completed -> citizen-verified.
       Every stage is a real COUNT query, never an invented/rounded figure.
    2. Community outcomes — every Project with a Verified Outcome in this
       country, showing the government-reported before/after (Outcome) and
       the separate citizen-verification signal (DemandCluster.community_
       sentiment) side by side, same "never merge official vs. community
       signals" discipline as evidence_detail.html.

    Logged-in citizens additionally see a personal contribution summary
    (reports submitted / demands joined / resolved) reusing the same
    identity pattern as my_timeline().
    """
    country_id = _country_id_from_session()

    # --- Funnel (country-wide, real counts only) ---
    citizen_voices = (
        db.session.query(func.count(func.distinct(
            case(
                (Contribution.citizen_account_id.isnot(None), func.concat("acct:", Contribution.citizen_account_id)),
                else_=func.concat("anon:", Contribution.anonymous_token),
            )
        )))
        .join(DemandCluster, Contribution.demand_cluster_id == DemandCluster.id)
        .filter(DemandCluster.country_id == country_id)
        .scalar() or 0
    )
    collective_demands = DemandCluster.query.filter_by(country_id=country_id).count()

    # Every stage after this one counts DISTINCT demand clusters that
    # reached that stage, not raw row counts -- a cluster can accumulate
    # multiple GovernmentDecision rows over time (e.g. NeedsValidation,
    # then later Prioritize), which would otherwise make "government
    # decisions" exceed "collective demands" and break the funnel's basic
    # visual/logical premise (each stage should be <= the one before it).
    government_decisions = (
        db.session.query(func.count(func.distinct(GovernmentDecision.demand_cluster_id)))
        .filter(GovernmentDecision.country_id == country_id)
        .scalar() or 0
    )
    projects_total = (
        db.session.query(func.count(func.distinct(Project.linked_demand_cluster_id)))
        .filter(Project.country_id == country_id, Project.linked_demand_cluster_id.isnot(None))
        .scalar() or 0
    )
    projects_completed = (
        db.session.query(func.count(func.distinct(Project.linked_demand_cluster_id)))
        .filter(Project.country_id == country_id, Project.status == "Completion",
                Project.linked_demand_cluster_id.isnot(None))
        .scalar() or 0
    )
    citizen_verified_count = (
        db.session.query(func.count(func.distinct(Outcome.demand_cluster_id)))
        .join(Project, Outcome.project_id == Project.id)
        .filter(Project.country_id == country_id, Outcome.status == "Verified")
        .scalar() or 0
    )

    funnel = [
        {"label": "Citizen voices", "count": citizen_voices},
        {"label": "Collective demands", "count": collective_demands},
        {"label": "Government decisions", "count": government_decisions},
        {"label": "Projects", "count": projects_total},
        {"label": "Completed", "count": projects_completed},
        {"label": "Citizen-verified", "count": citizen_verified_count},
    ]

    # --- Community outcomes: every Verified Outcome in this country ---
    verified_outcomes = (
        Outcome.query
        .join(Project, Outcome.project_id == Project.id)
        .filter(Project.country_id == country_id, Outcome.status == "Verified")
        .order_by(Outcome.timestamp.desc())
        .limit(20)
        .all()
    )
    outcome_cards = []
    for outcome in verified_outcomes:
        project = db.session.get(Project, outcome.project_id)
        cluster = db.session.get(DemandCluster, outcome.demand_cluster_id)
        cat = db.session.get(Category, cluster.category_id) if cluster else None
        outcome_cards.append({
            "project": project,
            "cluster": cluster,
            "category_name": cat.name if cat else "",
            "category_code": cat.code if cat else "",
            "outcome": outcome,
            "sentiment": cluster.community_sentiment if cluster else None,
        })

    # --- Personal contribution summary (logged-in citizens only) ---
    personal = None
    if current_role() == "citizen" and current_actor_id():
        actor_id = current_actor_id()
        reports_count = Report.query.filter_by(citizen_account_id=actor_id).count()
        joined_count = Contribution.query.filter_by(citizen_account_id=actor_id).count()
        resolved_count = (
            db.session.query(func.count(func.distinct(Contribution.demand_cluster_id)))
            .join(Outcome, Outcome.demand_cluster_id == Contribution.demand_cluster_id)
            .filter(Contribution.citizen_account_id == actor_id, Outcome.status == "Verified")
            .scalar() or 0
        )
        personal = {
            "reports_submitted": reports_count,
            "demands_joined": joined_count,
            "issues_resolved": resolved_count,
        }

    return render_template(
        "citizen/impact.html",
        funnel=funnel,
        outcome_cards=outcome_cards,
        personal=personal,
    )


# ---------------------------------------------------------------------------
# Community Verification (inline action from /community)
# ---------------------------------------------------------------------------

@citizen_bp.route("/cluster/<cluster_id>/support", methods=["POST"])
def support_cluster(cluster_id):
    """
    "I need this too" — the Community page's one-click affordance for
    joining an EXISTING collective demand without going through the full
    AI report-extraction pipeline. A citizen who recognises their own
    problem in an already-identified cluster shouldn't have to describe it
    again from scratch; they're confirming, not reporting something new.

    Still creates a minimal Report row (Contribution.report_id is NOT NULL,
    and "every contribution traces to a report" is a hard invariant
    elsewhere in this file/README) directly in Clustered status — this is
    the one place a Report is created without ever calling the AI
    extraction pipeline, which is fine because there is no free-text input
    to extract from.

    Idempotent per identity+cluster: a second click from the same citizen
    (or anonymous browser) is a no-op, not a second Contribution — matches
    DemandCluster.unique_contributors already deduping by identity_key, but
    without this guard total_reports would still inflate on repeat clicks.
    """
    cluster = DemandCluster.query.get_or_404(cluster_id)
    citizen_account_id, anonymous_token = _current_identity()

    existing_query = Contribution.query.filter_by(demand_cluster_id=cluster_id)
    if citizen_account_id:
        existing_query = existing_query.filter_by(citizen_account_id=citizen_account_id)
    else:
        existing_query = existing_query.filter_by(anonymous_token=anonymous_token)
    if existing_query.first() is not None:
        flash("You've already added your voice to this.", "info")
        return redirect(url_for("citizen.community"))

    report = Report(
        citizen_account_id=citizen_account_id,
        anonymous_token=anonymous_token,
        country_id=cluster.country_id,
        region_id=(cluster.region_ids[0] if cluster.region_ids else None),
        category_id=cluster.category_id,
        original_raw_input="Supported an existing community demand (\"I need this too\").",
        channel="text",
        status="Clustered",
    )
    db.session.add(report)
    db.session.flush()

    db.session.add(Contribution(
        report_id=report.id,
        citizen_account_id=citizen_account_id,
        anonymous_token=anonymous_token,
        demand_cluster_id=cluster_id,
        type="joined",
    ))
    db.session.commit()
    flash("Added — thanks for confirming this affects you too.", "success")
    return redirect(url_for("citizen.community"))


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

    citizen_account_id, anonymous_token = _current_identity()
    db.session.add(Verification(
        citizen_account_id=citizen_account_id,
        anonymous_token=anonymous_token,
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


def _groq_model_name() -> str:
    """Mirrors groq_client._model() — kept in sync so audit logs record the
    model actually used, not a hardcoded guess."""
    import os
    return os.environ.get("GROQ_MODEL", "qwen/qwen3-27b")


def _current_identity():
    """
    Returns (citizen_account_id, anonymous_token) — exactly one is set.

    A logged-in citizen uses their real account id. An anonymous visitor
    gets a random token persisted in the signed session cookie, so they
    can keep tracking their own reports/contributions/votes across visits
    on the same browser without ever creating an account — the whole point
    of "no login required" for citizen reporting (see README).
    """
    user = current_user()
    if user is not None and current_account_type() == "citizen":
        return user.id, None

    token = session.get("anon_token")
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        session["anon_token"] = token
        session.permanent = True
    return None, token


# Ingestion & Safety layer (Government/Citizen architecture docs, Layer 2 —
# "Rate Limiting", and the "Trust & Authenticity Layer" duplicate-detection
# feature). Deliberately scoped to what's actually justified for an MVP:
# catching the SAME citizen submitting the SAME text again a short time
# later (an accidental double-click, or a resubmission attempt) — not a
# blanket cooldown between any two reports, which would wrongly block a
# citizen filing two genuinely different problems back to back.
_DUPLICATE_WINDOW_MINUTES = 5


def _guard_against_spam_and_duplicates(raw_text: str):
    """
    Returns a Flask response (redirect) if this submission is an exact
    duplicate of the same identity's recent report, or None if it's fine
    to proceed. Works for both real accounts and anonymous visitors — the
    anonymous_token from _current_identity() is a stable per-browser
    identity, not a shared "anon" bucket, so self-duplication can be
    checked for anonymous submitters too.
    """
    citizen_account_id, anonymous_token = _current_identity()
    normalized = " ".join(raw_text.split()).lower()
    if not normalized:
        return None

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_DUPLICATE_WINDOW_MINUTES)
    query = Report.query.filter(Report.created_at >= cutoff)
    if citizen_account_id:
        query = query.filter(Report.citizen_account_id == citizen_account_id)
    else:
        query = query.filter(Report.anonymous_token == anonymous_token)
    recent = query.order_by(Report.created_at.desc()).limit(20).all()
    for r in recent:
        if " ".join(r.original_raw_input.split()).lower() == normalized:
            flash("You already reported this — here's where it stands.", "info")
            return redirect(url_for("citizen.demand_result", report_id=r.id))

    return None


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
    except Exception as e:
        # Embedding failure doesn't block cluster creation — rollback the
        # embedding write only, not the cluster itself. Queue a retry so
        # the cluster doesn't stay permanently unmatchable via vector
        # search just because Cohere hiccuped once.
        db.session.rollback()
        logger.warning("store_cluster_embedding failed (cluster still created): %s", e)
        db.session.add(cluster)
        db.session.flush()   # re-flush so cluster.id is valid again after rollback
        from app.services.job_queue import enqueue_job
        enqueue_job("retry_cluster_embedding", {
            "cluster_id": cluster.id, "summary_text": report.original_raw_input,
        })

    # Set centroid from the founding report's GPS, if available.
    # MUST run after the embedding try/except above — store_cluster_embedding()
    # commits internally, so anything written before its potential rollback
    # would be lost. This UPDATE is independent of embedding outcome.
    if report.latitude is not None and report.longitude is not None:
        from sqlalchemy import text as _text
        db.session.execute(
            _text(
                "UPDATE demand_clusters "
                "SET centroid = ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) "
                "WHERE id = :id"
            ),
            {"lng": report.longitude, "lat": report.latitude, "id": cluster.id},
        )

    return cluster


def _add_contribution(report: Report, cluster_id: str, contrib_type: str):
    """Add a Contribution and update the Report status to Clustered."""
    contrib = Contribution(
        report_id=report.id,
        citizen_account_id=report.citizen_account_id,
        anonymous_token=report.anonymous_token,
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
