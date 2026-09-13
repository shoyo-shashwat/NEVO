# services/email_client.py
#
# Real SMTP email delivery — works with any SMTP-compatible provider
# (SendGrid, Mailgun, Amazon SES, Postmark, or a plain mailbox) since none
# of them require a proprietary SDK for basic sends.
#
# Required environment variables (see README):
#   SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_ADDRESS
#   SMTP_USE_TLS   — "true"/"false", default "true"
#
# If SMTP_HOST is not set, send_email() does NOT pretend to succeed — it
# returns False and logs the message that would have been sent (including
# the reset/invite link) so local development and no-credential deployments
# stay usable without silently faking delivery. Callers must treat False as
# "email not configured" and surface that honestly (e.g. show the reset
# link on-screen in that case) rather than telling the user "check your
# inbox" when nothing was sent.

import os
import smtplib
import logging
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send_email(to_address: str, subject: str, body_text: str) -> bool:
    """
    Send a plain-text email. Returns True if actually handed off to the
    SMTP server, False if SMTP is not configured or the send failed.

    Never raises — a failed/unconfigured email must never break the
    calling request (signup, password reset, invite).
    """
    if not _smtp_configured():
        logger.warning(
            "SMTP not configured (SMTP_HOST unset) — email not sent. "
            "to=%s subject=%r body=%r",
            to_address, subject, body_text,
        )
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_address = os.environ.get("SMTP_FROM_ADDRESS", username or "no-reply@localhost")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to_address
    msg.set_content(body_text)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.error("Email send failed to %s: %s", to_address, e)
        return False
