# models/whatsapp_models.py
#
# WhatsAppMessageLog — the durability/dedup record for the Twilio WhatsApp
# webhook (app/whatsapp/routes.py). Twilio may redeliver a webhook it didn't
# get a fast/2xx response to, so MessageSid is the idempotency key: a second
# delivery of the same message must never create a second Report.
#
# from_number_hash stores a SHA-256 hash of the sender's WhatsApp number, not
# the number itself — enough to dedupe/rate-limit per sender without holding
# a citizen's raw phone number in a log table that has no other purpose.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


class WhatsAppMessageLog(db.Model):
    __tablename__ = "whatsapp_message_logs"
    __table_args__ = (
        db.UniqueConstraint("message_sid", name="uq_whatsapp_message_sid"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    message_sid = db.Column(db.String(64), nullable=False, index=True)
    from_number_hash = db.Column(db.String(64), nullable=False, index=True)

    status = db.Column(
        db.Enum("received", "processed", "failed", name="whatsapp_status_enum"),
        nullable=False, default="received",
    )
    report_id = db.Column(db.String(36), db.ForeignKey("reports.id"), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # Set only on the turn a contact explicitly asks to switch language
    # ("reply in Hindi" etc.) — the most recent non-null value for a given
    # from_number_hash is that contact's standing preference, read back by
    # app/whatsapp/routes.py::_get_preferred_language(). Most rows leave
    # this null (no language switch requested that turn).
    preferred_language = db.Column(db.String(10), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    report = db.relationship("Report", foreign_keys=[report_id])

    def __repr__(self):
        return f"<WhatsAppMessageLog {self.message_sid} status={self.status}>"
