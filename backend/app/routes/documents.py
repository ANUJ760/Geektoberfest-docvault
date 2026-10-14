from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import uuid
import shutil
from .. import models, schemas, auth
from ..database import get_db
from ..audit_logger import log_event
from ..access import get_owned_document, get_owned_folder

router = APIRouter()
UPLOAD_DIR = "uploads"

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10 MB

@router.post("/upload", response_model=schemas.DocumentResponse)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    folder_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Read file size
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 10MB.")

    # Check folder if provided
    if folder_id:
        folder = get_owned_folder(db, folder_id, current_user)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found or you don't have access")

    # Generate unique filename to avoid conflicts
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    new_doc = models.Document(
        filename=unique_filename,
        original_filename=file.filename,
        file_path=file_path,
        file_size=file_size,
        content_type=file.content_type,
        folder_id=folder_id,
        owner_id=current_user.id
    )

    db.add(new_doc)
    db.flush()  # assign new_doc.id before logging

    log_event(
        db,
        user_id=current_user.id,
        action="upload",
        target_type="document",
        target_id=new_doc.id,
        target_name=file.filename,
        ip_address=request.client.host if request.client else None,
    )

    db.commit()
    db.refresh(new_doc)
    return new_doc

# fixed : 
@router.get("/", response_model=List[schemas.DocumentResponse])
def get_documents(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    file_type: Optional[str] = None,
    sort_by: Optional[str] = "date_desc",  # NEW
    folder_id: Optional[int] = None,
):
    query = db.query(models.Document).filter(models.Document.owner_id == current_user.id)

    # Folder filter: a folder you don't own is a 404, not an empty list
    if folder_id is not None:
        if not get_owned_folder(db, folder_id, current_user):
            raise HTTPException(status_code=404, detail="Folder not found")
        query = query.filter(models.Document.folder_id == folder_id)

    # File type filter
    if file_type and file_type.lower() != "all":
        query = query.filter(models.Document.content_type.ilike(f"%{file_type}%"))

    # Sorting options (Issue #7)
    if sort_by == "name_asc":
        query = query.order_by(models.Document.original_filename.asc())
    elif sort_by == "name_desc":
        query = query.order_by(models.Document.original_filename.desc())
    elif sort_by == "date_asc":
        query = query.order_by(models.Document.created_at.asc())
    else:
        # Default: newest first
        query = query.order_by(models.Document.created_at.desc())

    return query.all()
@router.get("/search", response_model=List[schemas.DocumentResponse])
def search_documents(q: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    if not q:
        return []
    # Search by original_filename, only for this user
    return db.query(models.Document).filter(
        models.Document.owner_id == current_user.id,
        models.Document.original_filename.ilike(f"%{q}%")
    ).all()

@router.get("/{doc_id}/download")
def download_document(request: Request, doc_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    doc = get_owned_document(db, doc_id, current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    log_event(
        db,
        user_id=current_user.id,
        action="download",
        target_type="document",
        target_id=doc.id,
        target_name=doc.original_filename,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    return FileResponse(
        path=doc.file_path,
        filename=doc.original_filename,
        media_type=doc.content_type
    )

def detect_preview_content_type(file_path: str) -> str:
    """
    Inspect file bytes and header to determine safe preview eligibility.
    Returns canonical content type if eligible.
    Raises HTTPException(415) if type is unsupported or unsafe.
    """
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    size = os.path.getsize(file_path)
    if size > 10 * 1024 * 1024:
        status_code = getattr(status, "HTTP_415_UNSUPPORTED_MEDIA_TYPE", 415)
        raise HTTPException(
            status_code=status_code,
            detail="File is too large for inline preview. Please download to view.",
        )

    # Read leading bytes for signature check
    with open(file_path, "rb") as f:
        header = f.read(1024)

    # PNG magic bytes
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # JPEG magic bytes
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # PDF magic bytes
    if header.startswith(b"%PDF-"):
        return "application/pdf"

    # Check for active HTML/SVG/XML tags to reject active content
    lower_header = header.lower().strip()
    active_signatures = [
        b"<html", b"<!doctype html", b"<script", b"<svg", b"<?xml",
        b"<iframe", b"<body", b"<head", b"<embed", b"<object"
    ]
    if any(sig in lower_header for sig in active_signatures):
        status_code = getattr(status, "HTTP_415_UNSUPPORTED_MEDIA_TYPE", 415)
        raise HTTPException(
            status_code=status_code,
            detail="Active content (HTML/SVG/XML) is not permitted for inline preview. Please download to view.",
        )

    # Check for text/plain: verify text decodes cleanly
    try:
        header.decode("utf-8")
        # Ensure file content up to 1MB decodes cleanly
        if size <= 1024 * 1024:
            with open(file_path, "rb") as f:
                f.read().decode("utf-8")
        return "text/plain; charset=utf-8"
    except UnicodeDecodeError:
        pass

    status_code = getattr(status, "HTTP_415_UNSUPPORTED_MEDIA_TYPE", 415)
    raise HTTPException(
        status_code=status_code,
        detail="File type is not supported for inline preview. Please download the document instead.",
    )

@router.get("/{doc_id}/preview")
def preview_document(
    request: Request,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    doc = get_owned_document(db, doc_id, current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    canonical_type = detect_preview_content_type(doc.file_path)

    log_event(
        db,
        user_id=current_user.id,
        action="preview",
        target_type="document",
        target_id=doc.id,
        target_name=doc.original_filename,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    headers = {
        "Content-Disposition": f'inline; filename="{doc.original_filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store, no-cache, must-revalidate",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }

    return FileResponse(
        path=doc.file_path,
        media_type=canonical_type,
        headers=headers,
    )

@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(request: Request, doc_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    doc = get_owned_document(db, doc_id, current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Capture name before deletion so the audit record is meaningful
    doc_name = doc.original_filename

    # Remove file from disk
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    db.delete(doc)
    db.flush()

    log_event(
        db,
        user_id=current_user.id,
        action="delete",
        target_type="document",
        target_id=doc_id,
        target_name=doc_name,
        ip_address=request.client.host if request.client else None,
    )

    db.commit()
    return None
