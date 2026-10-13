from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter()


@router.get("/", response_model=List[schemas.AuditLogResponse])
def get_audit_logs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    Return audit events that belong to the authenticated user, newest first.
    Only the user who performed the action can see their own log entries.
    """
    return (
        db.query(models.AuditLog)
        .filter(models.AuditLog.user_id == current_user.id)
        .order_by(models.AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
