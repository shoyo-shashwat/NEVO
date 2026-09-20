# whatsapp/routes.py
#
# Twilio WhatsApp citizen-intake webhook.
#
# Flow: Twilio inbound message -> signature verification -> dedupe by
# MessageSid -> (voice) ElevenLabs transcription -> Groq structured
# extraction -> Cohere embedding + pgvector similarity match
# (app/services/demand_matching.py, the SAME service the citizen web app
# uses) -> a real Report/DemandCluster/Contribution -> TwiML confirmation.
#
# This channel makes one automatic join-vs-new-cluster decision instead of
# the web app's manual "join / show candidates / start new" screen, because
# plain WhatsApp text has no equivalent interactive picker:
#   auto_suggest tier (very high similarity)  -> join that cluster automatically
#   show_candidates / no_match tier           -> start a new cluster
# That is the one deliberate UX simplification here — the AI extraction,
# embedding, similarity search, and clustering underneath are unchanged from
# the real pipeline (no fake/stubbed matching).
#
# Twilio credentials required for REAL delivery: TWILIO_ACCOUNT_SID,
# TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER (see .env.example). Without a
# TWILIO_AUTH_TOKEN, the webhook still runs but cannot verify the caller is
# actually Twilio — see _validate_twilio_signature(). Use /whatsapp/test-send
# (gated behind WHATSAPP_TEST_ADAPTER=1) to exercise the real intake
# pipeline locally without any Twilio credentials at all.

import hashlib
import logging
import os
from datetime import datetime, timezone

from flask import request, Response, jsonify
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from app.whatsapp import whatsapp_bp
from app.extensions import db, csrf
from app.models.shared import Country, AdministrativeRegion, Category, EventLog
from app.models.citizen_models import Report, Contribution
from app.models.demand_cluster import DemandCluster
from app.models.whatsapp_models import WhatsAppMessageLog
from app.models.ai_models import log_ai_call
from app.services import groq_client
from app.services.demand_matching import find_similar_clusters, store_cluster_embedding

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:32]


def _twiml(message: str) -> Response:
    resp = MessagingResponse()
    resp.message(message)
    return Response(str(resp), mimetype="text/xml")


def _validate_twilio_signature() -> bool:
    """
    True if this request is verified as genuinely from Twilio, OR if no
    TWILIO_AUTH_TOKEN is configured at all (local/dev — where the /test-send
    adapter is the supported way to simulate messages instead). False ONLY
    when an auth token IS configured and the signature check actually fails
    — that is the one case a request must be rejected outright, so a forged
    POST can never create a Report.
    """
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN not set — WhatsApp webhook signature NOT verified (dev mode).")
        return True
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(auth_token)
    return validator.validate(request.url, request.form.to_dict(), signature)


def _transcribe_voice_media(media_url: str) -> str:
    """Downloads a WhatsApp voice-note media file (Twilio-authenticated) and
    transcribes it with the same ElevenLabs pipeline the web report flow uses."""
    import httpx
    from app.services.elevenlabs_client import transcribe_audio

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    resp = httpx.get(media_url, auth=(account_sid, auth_token), timeout=30.0)
    resp.raise_for_status()
    result = transcribe_audio(resp.content, mime_type=resp.headers.get("content-type") or "audio/ogg")
    return result["text"]


# ---------------------------------------------------------------------------
# Real intake pipeline — shared by the live Twilio webhook and the local
# test adapter, so both exercise the exact same extraction/matching logic.
# ---------------------------------------------------------------------------

def _process_intake_message(raw_text: str, from_number: str, log_row: WhatsAppMessageLog) -> str:
    country = Country.query.filter_by(code="IN").first()
    country_id = country.id if country else None
    anonymous_token = f"whatsapp:{log_row.from_number_hash}"

    try:
        extracted = groq_client.extract_report_fields(raw_text)
    except Exception as e:
        logger.warning("WhatsApp Groq extraction failed, falling back to Draft: %s", e)
        extracted = {
            "category": None, "location": None, "severity": None, "duration": None,
            "affected_group": None, "problem_summary": None, "problem_summary_en": None,
            "language_detected": None, "meta": {"complete": False, "missing_fields": ["category", "location"]},
        }
    meta = extracted.get("meta", {})

    category_id = None
    if extracted.get("category"):
        cat = Category.query.filter_by(code=extracted["category"]).first()
        category_id = cat.id if cat else None

    region_id = None
    if extracted.get("location") and country_id:
        region = (
            AdministrativeRegion.query.filter_by(country_id=country_id)
            .filter(AdministrativeRegion.name.ilike(f"%{extracted['location']}%"))
            .first()
        )
        region_id = region.id if region else None

    status = "Unclustered" if meta.get("complete") else "Draft"

    report = Report(
        anonymous_token=anonymous_token,
        consent_given_at=datetime.now(timezone.utc),
        country_id=country_id or "country-in",
        region_id=region_id,
        category_id=category_id,
        original_raw_input=raw_text,              # write-once, same invariant as the web route
        original_language=extracted.get("language_detected"),
        problem_summary_en=extracted.get("problem_summary_en"),
        channel="messaging",
        severity=extracted.get("severity"),
        duration=extracted.get("duration"),
        affected_group=extracted.get("affected_group"),
        status=status,
    )
    db.session.add(report)
    db.session.flush()
    log_row.report_id = report.id

    log_ai_call(report_id=report.id, stage="extraction", provider="groq", model=_groq_model_name(),
                success=bool(extracted.get("category") or extracted.get("problem_summary")),
                structured_output={k: v for k, v in extracted.items() if k != "meta"})
    db.session.add(EventLog(report_id=report.id, stage="Submitted"))
    db.session.add(EventLog(report_id=report.id, stage="AIUnderstood",
                             metadata_={"summary": extracted.get("problem_summary", "")}))

    if status == "Draft":
        try:
            clarification = groq_client.ask_clarification(raw_text, meta.get("missing_fields") or [])
        except Exception:
            clarification = "Could you share a bit more detail — what is the problem, and where is it happening?"
        db.session.commit()
        return clarification

    match_result = None
    if category_id and country_id:
        try:
            match_result = find_similar_clusters(
                report_text=extracted.get("problem_summary") or raw_text,
                category_id=category_id, country_id=country_id,
            )
        except Exception as e:
            logger.warning("WhatsApp demand matching failed, will start a new cluster: %s", e)

    if match_result and match_result.tier == "auto_suggest":
        cluster_id = match_result.matches[0].cluster_id
        db.session.add(Contribution(
            report_id=report.id, anonymous_token=anonymous_token,
            demand_cluster_id=cluster_id, type="joined",
        ))
        report.status = "Clustered"
        db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster_id, stage="JoinedDemand"))
        db.session.commit()
        cluster = db.session.get(DemandCluster, cluster_id)
        contributors = cluster.unique_contributors if cluster else None
        return (
            f"Thank you — this matches a community issue already reported by "
            f"{contributors or 'several'} other people nearby. You've been added to it. "
            "We'll keep this linked to your number so you can be updated."
        )

    cluster = DemandCluster(
        country_id=country_id, region_ids=[region_id] if region_id else [],
        category_id=category_id, affected_localities=[], trend="stable", confidence="low",
        active_status="Active", review_status="NotReviewed",
    )
    db.session.add(cluster)
    db.session.flush()
    try:
        store_cluster_embedding(cluster.id, report.original_raw_input)
    except Exception as e:
        logger.warning("store_cluster_embedding failed for WhatsApp cluster (still created): %s", e)
    db.session.add(Contribution(
        report_id=report.id, anonymous_token=anonymous_token,
        demand_cluster_id=cluster.id, type="joined",
    ))
    report.status = "Clustered"
    db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster.id, stage="JoinedDemand"))
    db.session.commit()
    return (
        "Thank you — your report has been recorded as a new community issue. "
        "We'll let you know if other people report the same problem."
    )


def _groq_model_name() -> str:
    return os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@whatsapp_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    """Production Twilio WhatsApp inbound-message webhook."""
    if not _validate_twilio_signature():
        logger.warning("Rejected WhatsApp webhook — invalid Twilio signature.")
        return Response(status=403)

    message_sid = request.form.get("MessageSid", "")
    from_number = request.form.get("From", "")
    body = (request.form.get("Body") or "").strip()
    num_media = int(request.form.get("NumMedia", "0") or "0")

    if not message_sid or not from_number:
        return Response(status=400)

    # Duplicate-delivery guard — Twilio may retry a webhook that didn't get a
    # fast 2xx response. A second delivery of the same MessageSid must never
    # create a second Report.
    if WhatsAppMessageLog.query.filter_by(message_sid=message_sid).first() is not None:
        logger.info("Duplicate WhatsApp delivery for %s — not reprocessing.", message_sid)
        return _twiml("We already received this message — thank you.")

    log_row = WhatsAppMessageLog(
        message_sid=message_sid, from_number_hash=_phone_hash(from_number), status="received",
    )
    db.session.add(log_row)
    db.session.commit()
    logger.info("WhatsApp message received: sid=%s media=%s", message_sid, num_media)

    try:
        raw_text = body
        if num_media > 0 and not raw_text:
            media_type = request.form.get("MediaContentType0") or ""
            media_url = request.form.get("MediaUrl0") or ""
            if media_type.startswith("audio") and media_url:
                raw_text = _transcribe_voice_media(media_url)
            else:
                log_row.status = "failed"
                log_row.error_message = f"Unsupported media type: {media_type or 'unknown'}"
                db.session.commit()
                return _twiml(
                    "We can read text or voice notes right now. "
                    "Please send your community need as a text or voice message."
                )

        if not raw_text:
            log_row.status = "failed"
            log_row.error_message = "Empty message"
            db.session.commit()
            return _twiml("Please describe the community need you'd like to report.")

        reply_text = _process_intake_message(raw_text, from_number, log_row)
        log_row.status = "processed"
        db.session.commit()
        return _twiml(reply_text)

    except Exception as e:
        logger.exception("WhatsApp webhook processing failed for sid=%s", message_sid)
        log_row.status = "failed"
        log_row.error_message = f"{type(e).__name__}: {str(e)[:500]}"
        db.session.commit()
        return _twiml("Sorry, something went wrong processing your message. Please try again shortly.")


@whatsapp_bp.route("/test-send", methods=["POST"])
@csrf.exempt
def test_send():
    """
    LOCAL/DEV TEST ADAPTER — NOT real WhatsApp delivery.

    Simulates one inbound WhatsApp message by calling the exact same
    _process_intake_message() pipeline the real Twilio webhook uses (real
    Groq extraction, real Cohere embedding, real pgvector matching, real
    Report/DemandCluster/Contribution rows) without needing Twilio
    credentials, a public webhook URL, or a signature.

    Gated behind WHATSAPP_TEST_ADAPTER=1 so it can never be reachable in a
    deployment that hasn't explicitly opted in — this is a safety gate, not
    a default-on endpoint.

    POST JSON: {"from": "whatsapp:+91XXXXXXXXXX" (optional), "text": "..."}
    """
    if os.environ.get("WHATSAPP_TEST_ADAPTER") != "1":
        return Response(status=404)

    data = request.get_json(silent=True) or {}
    from_number = (data.get("from") or "whatsapp:+919999999999").strip()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    fake_sid = "test-" + hashlib.sha256((from_number + text + str(datetime.now(timezone.utc))).encode()).hexdigest()[:24]
    log_row = WhatsAppMessageLog(
        message_sid=fake_sid, from_number_hash=_phone_hash(from_number), status="received",
    )
    db.session.add(log_row)
    db.session.commit()

    try:
        reply = _process_intake_message(text, from_number, log_row)
        log_row.status = "processed"
        db.session.commit()
        return jsonify({"reply": reply, "report_id": log_row.report_id, "message_sid": fake_sid})
    except Exception as e:
        logger.exception("WhatsApp test-send failed")
        log_row.status = "failed"
        log_row.error_message = str(e)[:500]
        db.session.commit()
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/status-callback", methods=["POST"])
@csrf.exempt
def status_callback():
    """
    Twilio delivery-status callback — a separate webhook from /webhook,
    with a different payload (MessageStatus: queued/sent/delivered/read/
    failed/undelivered, no Body/NumMedia). Twilio only requires a 200
    response here; this just logs the status against the matching
    WhatsAppMessageLog row (by MessageSid) for observability. Never treats
    a status ping as an inbound citizen message — pointing Twilio's "When a
    message comes in" webhook at this route (or vice versa) would be wrong.
    """
    if not _validate_twilio_signature():
        logger.warning("Rejected WhatsApp status callback — invalid Twilio signature.")
        return Response(status=403)

    message_sid = request.form.get("MessageSid", "")
    message_status = request.form.get("MessageStatus", "")
    error_code = request.form.get("ErrorCode") or None

    logger.info("WhatsApp delivery status: sid=%s status=%s error_code=%s",
                message_sid, message_status, error_code)

    log_row = WhatsAppMessageLog.query.filter_by(message_sid=message_sid).first()
    if log_row is not None and message_status in ("failed", "undelivered") and error_code:
        log_row.error_message = f"Delivery {message_status}: Twilio error {error_code}"
        db.session.commit()

    return Response(status=200)
