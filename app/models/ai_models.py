# models/ai_models.py
#
# AIProcessingLog — the audit trail for every AI call the platform makes.
# Hard invariant (matches the AI pipeline requirement across groq_client /
# elevenlabs_client / cohere_client): AI output must be auditable — model,
# provider, version, confidence, timestamp, and the structured result it
# produced — without ever overwriting the citizen's original input
# (Report.original_raw_input stays write-once; this table is purely
# additive, never mutates a Report's own columns).
#
# BackgroundJob — a minimal Postgres-backed job queue. No new
# infrastructure (Redis, a message broker) is introduced — SELECT ... FOR
# UPDATE SKIP LOCKED gives safe concurrent claiming with what's already
# there. See worker.py for the run loop and app/services/job_queue.py for
# the enqueue/handler-registry API.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# AIProcessingLog
# ---------------------------------------------------------------------------

class AIProcessingLog(db.Model):
    __tablename__ = "ai_processing_logs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    report_id = db.Column(db.String(36), db.ForeignKey("reports.id"), nullable=True, index=True)

    stage = db.Column(
        db.Enum(
            "transcription", "extraction", "clarification",
            "embedding", "demand_matching", "decision_simplification",
            name="ai_stage_enum",
        ),
        nullable=False,
    )
    provider = db.Column(db.String(50), nullable=False)     # groq | elevenlabs | cohere
    model = db.Column(db.String(100), nullable=False)
    model_version = db.Column(db.String(50), nullable=True)

    success = db.Column(db.Boolean, nullable=False, default=True)
    confidence = db.Column(db.String(20), nullable=True)     # stage-dependent: "high", "0.88", etc.
    latency_ms = db.Column(db.Integer, nullable=True)

    # The actual parsed/structured result (or a safe summary of it) — the
    # thing that makes a classification auditable after the fact.
    structured_output = db.Column(db.JSON, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )

    report = db.relationship("Report", foreign_keys=[report_id])

    def __repr__(self):
        return f"<AIProcessingLog {self.stage} provider={self.provider} success={self.success}>"


def log_ai_call(
    stage: str, provider: str, model: str, *,
    report_id: str | None = None, model_version: str | None = None,
    success: bool = True, confidence: str | None = None,
    latency_ms: int | None = None, structured_output: dict | None = None,
    error_message: str | None = None,
) -> AIProcessingLog:
    """Convenience constructor — appends to db.session but does not commit."""
    entry = AIProcessingLog(
        report_id=report_id, stage=stage, provider=provider, model=model,
        model_version=model_version, success=success, confidence=confidence,
        latency_ms=latency_ms, structured_output=structured_output,
        error_message=error_message,
    )
    db.session.add(entry)
    return entry


# ---------------------------------------------------------------------------
# BackgroundJob
# ---------------------------------------------------------------------------

class BackgroundJob(db.Model):
    __tablename__ = "background_jobs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_type = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)

    status = db.Column(
        db.Enum("pending", "running", "succeeded", "failed", name="job_status_enum"),
        nullable=False, default="pending", index=True,
    )
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=5)
    last_error = db.Column(db.Text, nullable=True)

    # Jobs aren't run before this timestamp — lets failed jobs back off
    # (exponential-ish) instead of hammering a struggling provider.
    run_after = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f"<BackgroundJob {self.job_type} status={self.status} attempts={self.attempts}>"
