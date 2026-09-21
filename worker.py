# worker.py
#
# Background job worker — run as a separate process from the web app
# (see render.yaml for the Render "worker" service entry).
#
#   python worker.py
#
# Polls background_jobs for due, pending rows using SELECT ... FOR UPDATE
# SKIP LOCKED so multiple worker processes can run concurrently without
# double-processing a job. See app/services/job_queue.py for the enqueue
# side and the rationale for a Postgres-backed queue over Celery/Redis.
#
# Handlers are plain functions registered in HANDLERS below, each taking
# the job's payload dict and raising on failure (the run loop handles
# retry/backoff/failure bookkeeping — a handler should just do the work
# or raise).

import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models.ai_models import BackgroundJob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 5
BACKOFF_BASE_SECONDS = 30  # attempt N backs off N * this many seconds


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_send_email(payload: dict) -> None:
    from app.services.email_client import send_email
    ok = send_email(payload["to_address"], payload["subject"], payload["body_text"])
    if not ok:
        raise RuntimeError(f"send_email failed for {payload['to_address']!r}")


def _handle_retry_cluster_embedding(payload: dict) -> None:
    from app.services.demand_matching import store_cluster_embedding
    store_cluster_embedding(payload["cluster_id"], payload["summary_text"])


HANDLERS = {
    "send_email": _handle_send_email,
    "retry_cluster_embedding": _handle_retry_cluster_embedding,
}


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------

_shutdown = False


def _request_shutdown(signum, frame):
    global _shutdown
    logger.info("Shutdown requested (signal %s) — finishing current job, then exiting.", signum)
    _shutdown = True


def claim_next_job() -> BackgroundJob | None:
    """
    Atomically claim one due, pending job. FOR UPDATE SKIP LOCKED means a
    second worker process polling at the same instant simply skips rows
    already locked by this one, instead of blocking or double-claiming.
    """
    now = datetime.now(timezone.utc)
    job = (
        BackgroundJob.query
        .filter(BackgroundJob.status == "pending", BackgroundJob.run_after <= now)
        .order_by(BackgroundJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None

    job.status = "running"
    job.started_at = now
    job.attempts += 1
    db.session.commit()
    return job


def process_job(job: BackgroundJob) -> None:
    handler = HANDLERS.get(job.job_type)
    if handler is None:
        job.status = "failed"
        job.last_error = f"No handler registered for job_type={job.job_type!r}"
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.error(job.last_error)
        return

    try:
        handler(job.payload)
    except Exception as e:
        logger.warning("Job %s (%s) failed on attempt %d: %s", job.id, job.job_type, job.attempts, e)
        job.last_error = str(e)[:2000]
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            logger.error("Job %s (%s) exhausted %d attempts — giving up.", job.id, job.job_type, job.max_attempts)
        else:
            job.status = "pending"
            job.run_after = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_BASE_SECONDS * job.attempts)
        db.session.commit()
        return

    job.status = "succeeded"
    job.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    logger.info("Job %s (%s) succeeded on attempt %d.", job.id, job.job_type, job.attempts)


def run():
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    app = create_app()
    logger.info("Worker started — polling every %ss.", POLL_INTERVAL_SECONDS)

    with app.app_context():
        while not _shutdown:
            try:
                job = claim_next_job()
            except Exception as e:
                logger.error("Error claiming job (will retry): %s", e)
                db.session.rollback()
                job = None

            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            process_job(job)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    run()
    sys.exit(0)
