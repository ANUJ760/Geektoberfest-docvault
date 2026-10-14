"""
Tests for document download integrity checks and safe error handling (Issue #32).
"""

import hashlib
import io
import os
import stat
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, models
from app.database import Base, get_db
from app.routes import documents, audit


def _build_app(db_session):
    def test_db():
        yield db_session

    app = FastAPI()
    app.include_router(documents.router, prefix="/documents")
    app.include_router(audit.router, prefix="/audit")
    app.dependency_overrides[get_db] = test_db
    return app


class DownloadIntegrityTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.alice = models.User(username="alice", email="alice@example.com", hashed_password="unused")
        self.bob = models.User(username="bob", email="bob@example.com", hashed_password="unused")
        self.db.add_all([self.alice, self.bob])
        self.db.commit()
        self.db.refresh(self.alice)
        self.db.refresh(self.bob)

        self.upload_dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(documents, "UPLOAD_DIR", self.upload_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.upload_dir.cleanup)

        self.app = _build_app(self.db)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.headers["Authorization"] = "Bearer " + auth.create_access_token({"sub": self.alice.username})

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def _create_doc(self, owner, filename="test.txt", content=b"sample content", content_sha256=None):
        file_path = os.path.join(self.upload_dir.name, filename)
        with open(file_path, "wb") as f:
            f.write(content)

        doc = models.Document(
            filename=filename,
            original_filename=filename,
            file_path=file_path,
            file_size=len(content),
            content_type="text/plain",
            owner_id=owner.id,
        )
        if content_sha256 is not None:
            # If the model has content_sha256 or as dynamic attribute
            setattr(doc, "content_sha256", content_sha256)
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def _count_audit_logs(self, action="download"):
        return self.db.query(models.AuditLog).filter(models.AuditLog.action == action).count()

    def test_successful_download_bytes_headers_and_audit(self):
        content = b"Hello, secure download!"
        doc = self._create_doc(self.alice, "hello.txt", content)

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, content)
        self.assertIn("text/plain", resp.headers.get("content-type", ""))
        self.assertIn("hello.txt", resp.headers.get("content-disposition", ""))
        self.assertEqual(self._count_audit_logs(), 1)

    def test_missing_file_returns_404_without_audit(self):
        doc = self._create_doc(self.alice, "missing.txt", b"temp")
        os.remove(doc.file_path)

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("File not found on server", resp.json()["detail"])
        self.assertEqual(self._count_audit_logs(), 0)

    def test_foreign_document_download_returns_404_without_audit(self):
        doc = self._create_doc(self.bob, "bob_secret.txt", b"secret")

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["detail"], "Document not found")
        self.assertEqual(self._count_audit_logs(), 0)

    def test_unauthenticated_request_rejected(self):
        doc = self._create_doc(self.alice, "doc.txt", b"hello")
        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._count_audit_logs(), 0)

    def test_path_traversal_outside_upload_root_rejected(self):
        doc = self._create_doc(self.alice, "valid.txt", b"hello")
        # Tamper doc.file_path to point outside upload_dir
        doc.file_path = "/etc/passwd"
        self.db.commit()

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Unsafe file path detected", resp.json()["detail"])
        self.assertEqual(self._count_audit_logs(), 0)

    def test_directory_path_rejected(self):
        sub_dir = os.path.join(self.upload_dir.name, "subdir")
        os.makedirs(sub_dir, exist_ok=True)
        doc = models.Document(
            filename="subdir",
            original_filename="subdir",
            file_path=sub_dir,
            file_size=0,
            content_type="text/plain",
            owner_id=self.alice.id,
        )
        self.db.add(doc)
        self.db.commit()

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Requested document is not a regular file", resp.json()["detail"])
        self.assertEqual(self._count_audit_logs(), 0)

    def test_unreadable_file_fails_without_leaking_path(self):
        doc = self._create_doc(self.alice, "unreadable.txt", b"secret data")
        # Remove read permissions
        os.chmod(doc.file_path, 0o000)
        try:
            resp = self.client.get(f"/documents/{doc.id}/download")
            self.assertEqual(resp.status_code, 500)
            detail = resp.json()["detail"]
            self.assertIn("Cannot read document file", detail)
            # Ensure internal filesystem path is NOT leaked
            self.assertNotIn(self.upload_dir.name, detail)
            self.assertEqual(self._count_audit_logs(), 0)
        finally:
            os.chmod(doc.file_path, 0o644)

    def test_file_size_mismatch_rejected_without_audit(self):
        doc = self._create_doc(self.alice, "tampered.txt", b"1234567890")
        # Corrupt file by appending extra bytes without updating doc.file_size
        with open(doc.file_path, "ab") as f:
            f.write(b"corrupted_extra_bytes")

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("size mismatch", resp.json()["detail"])
        self.assertEqual(self._count_audit_logs(), 0)

    def test_trusted_checksum_mismatch_rejected_without_audit(self):
        content = b"original content"
        correct_hash = hashlib.sha256(content).hexdigest()
        doc = self._create_doc(self.alice, "hashed.txt", content, content_sha256=correct_hash)

        # Equal-length corruption: replace content with different bytes of the same length
        corrupted_content = b"corruptedcontent"
        self.assertEqual(len(content), len(corrupted_content))
        with open(doc.file_path, "wb") as f:
            f.write(corrupted_content)

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("checksum mismatch", resp.json()["detail"])
        self.assertEqual(self._count_audit_logs(), 0)

    def test_trusted_checksum_match_succeeds(self):
        content = b"verified content"
        correct_hash = hashlib.sha256(content).hexdigest()
        doc = self._create_doc(self.alice, "verified.txt", content, content_sha256=correct_hash)

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, content)
        self.assertEqual(self._count_audit_logs(), 1)

    def test_legacy_file_without_checksum_succeeds(self):
        content = b"legacy file content"
        doc = self._create_doc(self.alice, "legacy.txt", content, content_sha256=None)

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, content)
        self.assertEqual(self._count_audit_logs(), 1)


if __name__ == "__main__":
    unittest.main()
