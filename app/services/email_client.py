# services/email_client.py
#
# Real SMTP integration boundary — not a fake "email sent" response.
#
# No email provider credentials exist yet (no SMTP_* vars in .env). Per the
# production-readiness requirement, this module implements the actual
# integration (stdlib smtplib, no vendor lock-in) and clearly raises
# EmailNotConfiguredError when credentials are absent, rather than silently
# pretending delivery succeeded. In that dev-fallback state the email body
# is logged instead so password-reset and notification flows are still
# exercisable end-to-end during development — the log line is explicitly
# not a delivery confirmation.

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(Exception):
    """SMTP_* environment variables are not set. Real credentials are the
    missing piece here, not the integration — see .env.example for the
    required keys (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM_EMAIL)."""


def _smtp_config() -> dict | None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_email": os.environ.get("SMTP_FROM_EMAIL", "no-reply@nevo.gov"),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() != "false",
    }


def send_email(to_email: str, subject: str, body: str) -> None:
    """
    Raises EmailNotConfiguredError if SMTP_* is not set in the environment —
    callers must catch this and give the user an honest message, never
    silently treat it as "sent".
    """
    config = _smtp_config()
    if config is None:
        logger.warning(
            "SMTP is not configured (SMTP_HOST unset) — email NOT sent. "
            "Dev fallback: logging intended content below.\nTo: %s\nSubject: %s\n%s",
            to_email, subject, body,
        )
        raise EmailNotConfiguredError(
            "Email delivery is not configured on this server (missing SMTP_HOST "
            "and related settings). Contact an administrator."
        )

    msg = EmailMessage()
    msg["From"] = config["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
        if config["use_tls"]:
            server.starttls()
        if config["user"]:
            server.login(config["user"], config["password"])
        server.send_message(msg)
