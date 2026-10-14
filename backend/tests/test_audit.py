"""
Tests for audit-log event creation and access control.

Run from the backend/ directory with:
    python -m unittest discover -s tests -v
"""

import io
import os
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


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------

def _build_app(db_session):
    """Return a minimal FastAPI test app wired to the given session."""
    def test_db():
        yield db_session

    app = FastAPI()
    app.include_router(documents.router, prefix="/documents")
    app.include_router(audit.router, prefix="/audit")
    app.dependency_overrides[get_db] = test_db
    return app


class AuditTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.db.add_all([
            models.User(username="alice", email="alice@example.com", hashed_password="unused"),
            models.User(username="bob",   email="bob@example.com",   hashed_password="unused"),
        ])
        self.db.commit()
        self.alice, self.bob = self.db.query(models.User).order_by(models.User.id).all()

        # Keep uploaded files out of the real uploads/ directory
        self.upload_dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(documents, "UPLOAD_DIR", self.upload_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.upload_dir.cleanup)

        self.app = _build_app(self.db)
        self.client = TestClient(self.app, raise_server_exceptions=True)
        self.client.headers["Authorization"] = self._auth(self.alice)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def _auth(self, user):
        return "Bearer " + auth.create_access_token({"sub": user.username})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _upload(self, filename="hello.txt", content=b"hello", as_user=None):
        headers = {}
        if as_user:
            headers["Authorization"] = self._auth(as_user)
        resp = self.client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(content), "text/plain")},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _seed_document(self, owner, filename="report.pdf"):
        """Insert a document row and an empty file for it in the temp upload dir."""
        doc = models.Document(
            filename=filename,
            original_filename=filename,
            file_path=os.path.join(self.upload_dir.name, filename),
            file_size=0,
            content_type="application/pdf",
            owner_id=owner.id,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        open(doc.file_path, "wb").close()
        return doc

    def _audit_logs(self, as_user=None):
        headers = {}
        if as_user:
            headers["Authorization"] = self._auth(as_user)
        return self.client.get("/audit/", headers=headers)


# ===========================================================================
# 1. Event-creation tests
# ===========================================================================

class TestAuditEventCreation(AuditTestBase):

    def test_upload_creates_audit_event(self):
        doc = self._upload("test.txt", b"data")
        logs = self._audit_logs().json()
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry["action"], "upload")
        self.assertEqual(entry["target_type"], "document")
        self.assertEqual(entry["target_id"], doc["id"])
        self.assertEqual(entry["target_name"], "test.txt")
        self.assertEqual(entry["user_id"], self.alice.id)
        self.assertIn("created_at", entry)

    def test_delete_creates_audit_event(self):
        doc = self._seed_document(self.alice, "delete_me.pdf")
        resp = self.client.delete(f"/documents/{doc.id}")
        self.assertEqual(resp.status_code, 204, resp.text)

        logs = self._audit_logs().json()
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry["action"], "delete")
        self.assertEqual(entry["target_type"], "document")
        self.assertEqual(entry["target_id"], doc.id)
        self.assertEqual(entry["target_name"], "delete_me.pdf")

    def test_download_creates_audit_event(self):
        doc = self._seed_document(self.alice, "get_me.pdf")

        resp = self.client.get(f"/documents/{doc.id}/download")
        self.assertEqual(resp.status_code, 200, resp.text)

        logs = self._audit_logs().json()
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry["action"], "download")
        self.assertEqual(entry["target_id"], doc.id)
        self.assertEqual(entry["target_name"], "get_me.pdf")

    def test_failed_download_is_not_logged(self):
        resp = self.client.get("/documents/999/download")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._audit_logs().json(), [])

    def test_multiple_actions_all_recorded(self):
        doc1 = self._upload("a.txt", b"aaa")
        doc2 = self._upload("b.txt", b"bbb")
        logs = self._audit_logs().json()
        actions = [l["action"] for l in logs]
        self.assertEqual(actions.count("upload"), 2)

    def test_audit_log_does_not_store_sensitive_fields(self):
        """AuditLogResponse must not expose passwords, tokens, or file bytes."""
        self._upload("sensitive.txt", b"secret content")
        logs = self._audit_logs().json()
        self.assertGreater(len(logs), 0)
        for entry in logs:
            forbidden = {"password", "hashed_password", "token", "content", "file_path"}
            self.assertTrue(
                forbidden.isdisjoint(entry.keys()),
                f"Sensitive key found in audit entry: {set(entry.keys()) & forbidden}",
            )

    def test_audit_log_newest_first(self):
        self._upload("first.txt", b"1")
        self._upload("second.txt", b"2")
        logs = self._audit_logs().json()
        self.assertEqual(logs[0]["target_name"], "second.txt")
        self.assertEqual(logs[1]["target_name"], "first.txt")


# ===========================================================================
# 2. Access-control tests
# ===========================================================================

class TestAuditAccessControl(AuditTestBase):

    def test_unauthenticated_request_is_rejected(self):
        resp = self.client.get("/audit/", headers={"Authorization": ""})
        self.assertEqual(resp.status_code, 401)

    def test_user_sees_only_own_events(self):
        # Alice uploads
        self._upload("alice_doc.txt", b"alice")
        # Bob uploads
        self._upload("bob_doc.txt", b"bob", as_user=self.bob)

        alice_logs = self._audit_logs(as_user=self.alice).json()
        bob_logs   = self._audit_logs(as_user=self.bob).json()

        alice_names = {l["target_name"] for l in alice_logs}
        bob_names   = {l["target_name"] for l in bob_logs}

        self.assertIn("alice_doc.txt", alice_names)
        self.assertNotIn("bob_doc.txt", alice_names)

        self.assertIn("bob_doc.txt", bob_names)
        self.assertNotIn("alice_doc.txt", bob_names)

    def test_user_cannot_see_another_users_delete_event(self):
        # Seed a document for Alice, then delete it as Alice
        doc = self._seed_document(self.alice, "private.pdf")
        self.client.delete(f"/documents/{doc.id}")

        # Bob should see no events
        bob_logs = self._audit_logs(as_user=self.bob).json()
        self.assertEqual(len(bob_logs), 0)

    def test_pagination_limit_and_offset(self):
        for i in range(5):
            self._upload(f"file{i}.txt", b"x")
        # limit=2
        resp = self.client.get("/audit/?limit=2")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)
        # offset=4 → only 1 remaining
        resp2 = self.client.get("/audit/?limit=10&offset=4")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(resp2.json()), 1)


if __name__ == "__main__":
    unittest.main()
