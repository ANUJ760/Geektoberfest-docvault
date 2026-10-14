import os
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException, status
from . import models

# Default quota: 100 MiB (100 * 1024 * 1024 bytes)
DEFAULT_STORAGE_QUOTA_BYTES = 100 * 1024 * 1024

def get_configured_quota_bytes() -> int:
    """Retrieve and validate the configured per-user storage quota in bytes."""
    env_val = os.getenv("USER_STORAGE_QUOTA_BYTES")
    if env_val is not None:
        try:
            quota = int(env_val)
            if quota <= 0:
                raise ValueError("USER_STORAGE_QUOTA_BYTES must be a positive integer")
            return quota
        except ValueError as err:
            raise ValueError(f"Invalid USER_STORAGE_QUOTA_BYTES: {env_val}") from err
    return DEFAULT_STORAGE_QUOTA_BYTES

def validate_quota_configuration():
    """Validate storage quota configuration on application startup."""
    quota = get_configured_quota_bytes()
    if quota <= 0:
        raise ValueError(f"Storage quota must be positive, got {quota}")
    return quota

def get_user_storage_usage(db: Session, user_id: int) -> int:
    """Calculate total authoritative storage used in bytes for a user across all folders."""
    total_bytes = (
        db.query(func.coalesce(func.sum(models.Document.file_size), 0))
        .filter(models.Document.owner_id == user_id)
        .scalar()
    )
    return int(total_bytes or 0)

def check_and_reserve_quota(db: Session, user_id: int, incoming_bytes: int) -> tuple[int, int, int]:
    """
    Atomically verify storage quota for an upload.
    Acquires an exclusive lock on the user row (with_for_update) to serialize concurrent uploads.
    Returns (used_bytes, quota_bytes, remaining_bytes) on success.
    Raises HTTPException(413) if the upload would exceed the quota.
    """
    # Lock the user record to prevent race conditions during concurrent uploads
    user = db.query(models.User).filter(models.User.id == user_id).with_for_update().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    quota_bytes = get_configured_quota_bytes()
    used_bytes = get_user_storage_usage(db, user_id)

    if used_bytes + incoming_bytes > quota_bytes:
        remaining_bytes = max(quota_bytes - used_bytes, 0)
        quota_mb = round(quota_bytes / (1024 * 1024), 2)
        used_mb = round(used_bytes / (1024 * 1024), 2)
        incoming_mb = round(incoming_bytes / (1024 * 1024), 2)
        status_code = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)
        raise HTTPException(
            status_code=status_code,
            detail=(
                f"Storage quota exceeded. Allowed quota: {quota_mb} MB ({quota_bytes} bytes), "
                f"currently used: {used_mb} MB ({used_bytes} bytes), "
                f"upload requires: {incoming_mb} MB ({incoming_bytes} bytes), "
                f"remaining: {remaining_bytes} bytes."
            ),
        )

    remaining_bytes = quota_bytes - (used_bytes + incoming_bytes)
    return used_bytes, quota_bytes, remaining_bytes
