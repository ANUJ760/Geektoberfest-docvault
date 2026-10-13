"""
audit_logger.py
---------------
Thin helper for recording audit events.

Rules:
- Never log passwords, tokens, or file contents.
- Only record actor (user_id), action string, target type/id/name, IP, and timestamp.
"""

from sqlalchemy.orm import Session
from . import models


def log_event(
    db: Session,
    *,
    user_id: int,
    action: str,
    target_type: str,
    target_id: int | None = None,
    target_name: str | None = None,
    ip_address: str | None = None,
) -> models.AuditLog:
    """
    Persist a single audit event and return the created row.

    Parameters
    ----------
    db          : Active SQLAlchemy session (caller owns commit lifecycle).
    user_id     : ID of the authenticated user performing the action.
    action      : Short verb string – "upload", "download", "delete",
                  "share_created", "share_revoked".
    target_type : Resource kind – "document", "folder".
    target_id   : Primary key of the affected resource (nullable if already
                  deleted before we can capture it).
    target_name : Human-readable label (e.g. original filename).
    ip_address  : Client IP from the request (optional).
    """
    entry = models.AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        ip_address=ip_address,
    )
    db.add(entry)
    # The caller (route handler) is responsible for committing.
    # We flush so the entry gets an id within the current transaction.
    db.flush()
    return entry
