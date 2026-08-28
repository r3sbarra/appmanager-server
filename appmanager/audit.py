"""
Append-only audit logging for security-relevant actions.

Writes to the ``audit_log`` table. Actions are recorded for install/uninstall,
DB permission grants/revokes, config changes, API key rotation, and more.
"""

import json
import logging
from typing import Any, Dict, Optional

from appmanager.database import db
from appmanager.models import AuditLog

logger = logging.getLogger("appmanager.audit")


def log_action(
    action: str,
    actor_type: str = "admin",
    actor_id: Optional[int] = None,
    app_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """
    Record an audit log entry. Best-effort: never raises (audit must not break
    the primary operation).
    """
    try:
        entry = AuditLog(
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            app_id=app_id,
            details_json=json.dumps(details) if details is not None else None,
        )
        db.session.add(entry)
        db.session.commit()
        return entry
    except Exception as e:
        logger.warning("Failed to write audit log for '%s': %s", action, e)
        db.session.rollback()
        return None


def query_audit(
    app_id: Optional[int] = None,
    action: Optional[str] = None,
    actor_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    Query the audit log with optional filters, newest first.
    """
    q = AuditLog.query
    if app_id is not None:
        q = q.filter_by(app_id=app_id)
    if action:
        q = q.filter_by(action=action)
    if actor_type:
        q = q.filter_by(actor_type=actor_type)
    return q.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).offset(offset).limit(limit).all()
