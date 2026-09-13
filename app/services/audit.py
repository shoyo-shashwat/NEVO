# services/audit.py
#
# Single write path for the audit_log table (Phase 0 design §7). Called at
# the point of the action, not inferred afterward from other tables.
#
# Deliberately a flat log, not event sourcing — see
# docs/superpowers/specs/2026-09-12-phase0-foundations-design.md §7.

from flask import request

from app.extensions import db
from app.models.accounts import AuditLog


def log_action(
    actor_type: str,
    actor_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
) -> None:
    """
    Writes one AuditLog row. Does not commit — caller's existing
    db.session.commit() for the surrounding action covers this row too, so
    an audit write never partially succeeds independent of the action it's
    logging.
    """
    ip = None
    try:
        ip = request.remote_addr
    except RuntimeError:
        pass  # no request context (e.g. called from a CLI command)

    db.session.add(AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip,
    ))
