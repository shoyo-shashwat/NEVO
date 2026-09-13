# services/job_queue.py
#
# Minimal Postgres-backed background job queue — enqueue side.
#
# Deliberately not Celery/RQ + Redis: this app has no message broker
# today, and adding one is real new infrastructure with its own
# credentials/ops burden. SELECT ... FOR UPDATE SKIP LOCKED (see
# worker.py::claim_next_job) gives safe concurrent job claiming on the
# Postgres database this app already requires — a well-established
# pattern, not a shortcut. If throughput ever demands a real broker,
# this module's call sites don't need to change, only what's behind them.
#
# Handlers are registered by job_type in worker.py — this module only
# knows how to write a BackgroundJob row.

import logging

from app.extensions import db
from app.models.ai_models import BackgroundJob

logger = logging.getLogger(__name__)


def enqueue_job(job_type: str, payload: dict, max_attempts: int = 5) -> BackgroundJob:
    """
    Create a pending BackgroundJob row. Does not commit — caller controls
    the transaction boundary (usually committing alongside the rest of the
    request's writes, so the job and its triggering data land together).
    """
    job = BackgroundJob(job_type=job_type, payload=payload, max_attempts=max_attempts)
    db.session.add(job)
    return job


def queue_email(to_address: str, subject: str, body_text: str) -> bool:
    """
    Queue an email for background delivery via worker.py's "send_email"
    handler, IF an SMTP server is actually configured. If it isn't, this
    returns False immediately (no network call attempted, no job created)
    so the caller can fall back to showing the content/link directly —
    exactly like app.services.email_client.send_email()'s own contract,
    just decoupled from blocking the request on SMTP latency.
    """
    from app.services.email_client import _smtp_configured

    if not _smtp_configured():
        return False

    enqueue_job("send_email", {
        "to_address": to_address, "subject": subject, "body_text": body_text,
    })
    return True
