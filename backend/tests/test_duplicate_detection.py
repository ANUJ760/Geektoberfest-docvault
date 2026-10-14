"""
Tests for duplicate document upload detection (Issue #31).
"""

import hashlib
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


def _build_app(db_session):
    def test_db():
        yield db_session

    app = FastAPI()
    app.include_router(documents.router, prefix="/documents")
    app.include_router(audit.router, prefix="/audit")
    app.dependency_overrides[get_db] = test_db
    return app


class DuplicateDetectionTestCase(unittest.TestCase):
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
        self.client = TestClient(self.app, raise_server_exceptions=True)
        self.client.headers["Authorization"] = "Bearer " + auth.create_access_token({"sub": self.alice.username})

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def _upload(self, filename="sample.txt", content=b"content", as_user=None):
        headers = {}
        if as_user:
            headers["Authorization"] = "Bearer " + auth.create_access_token({"sub": as_user.username})
        return self.client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(content), "text/plain")},
            headers=headers,
        )

    def test_first_upload_is_not_duplicate(self):
        resp = self._upload("first.txt", b"Unique file content")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["is_duplicate"])
        self.assertIsNone(data["duplicate_of"])
        self.assertNotIn("X-Document-Duplicate", resp.headers)

        # Verify content_sha256 was saved in database
        doc = self.db.query(models.Document).filter(models.Document.id == data["id"]).first()
        expected_hash = hashlib.sha256(b"Unique file content").hexdigest()
        self.assertEqual(doc.content_sha256, expected_hash)

    def test_same_bytes_different_filename_detected_as_duplicate(self):
        content = b"Same document content for duplicate test"
        resp1 = self._upload("original.txt", content)
        self.assertEqual(resp1.status_code, 200)

        # Upload exact same bytes with a completely different filename
        resp2 = self._upload("copied_renamed.txt", content)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()

        # Upload succeeds (intentional copies allowed), with advisory warning
        self.assertTrue(data2["is_duplicate"])
        self.assertEqual(data2["duplicate_of"], "original.txt")
        self.assertEqual(resp2.headers.get("X-Document-Duplicate"), "true")
        self.assertEqual(resp2.headers.get("X-Duplicate-Of"), "original.txt")

    def test_different_bytes_same_filename_not_duplicate(self):
        self._upload("notes.txt", b"First version")
        resp = self._upload("notes.txt", b"Different content in second file")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["is_duplicate"])
        self.assertIsNone(data["duplicate_of"])

    def test_cross_user_privacy_never_discloses_other_users_duplicates(self):
        content = b"Confidential financial statement"
        # Alice uploads first
        resp_alice = self._upload("alice_finances.txt", content, as_user=self.alice)
        self.assertEqual(resp_alice.status_code, 200)

        # Bob uploads identical bytes
        resp_bob = self._upload("bob_finances.txt", content, as_user=self.bob)
        self.assertEqual(resp_bob.status_code, 200)
        data_bob = resp_bob.json()

        # Bob must NOT be told Alice has this file (no cross-user disclosure)
        self.assertFalse(data_bob["is_duplicate"])
        self.assertIsNone(data_bob["duplicate_of"])
        self.assertNotIn("X-Document-Duplicate", resp_bob.headers)

    def test_empty_file_handled(self):
        resp1 = self._upload("empty1.txt", b"")
        self.assertEqual(resp1.status_code, 200)
        self.assertFalse(resp1.json()["is_duplicate"])

        resp2 = self._upload("empty2.txt", b"")
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["is_duplicate"])
        self.assertEqual(resp2.json()["duplicate_of"], "empty1.txt")

    def test_binary_file_handled(self):
        binary_data = bytes(range(256)) * 4
        resp1 = self._upload("data1.bin", binary_data)
        self.assertEqual(resp1.status_code, 200)

        resp2 = self._upload("data2.bin", binary_data)
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["is_duplicate"])

    def test_legacy_documents_without_hash_do_not_cause_false_matches(self):
        # Insert a legacy document with content_sha256 = None
        legacy = models.Document(
            filename="legacy.txt",
            original_filename="legacy.txt",
            file_path=os.path.join(self.upload_dir.name, "legacy.txt"),
            file_size=10,
            content_type="text/plain",
            owner_id=self.alice.id,
            content_sha256=None
        )
        self.db.add(legacy)
        self.db.commit()

        # Alice uploads a new file
        resp = self._upload("new.txt", b"brand new content")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["is_duplicate"])

    def test_db_failure_cleans_up_file_and_rolls_back(self):
        content = b"Content that triggers failure"
        with mock.patch.object(self.db, "commit", side_effect=Exception("Simulated DB error")):
            with self.assertRaises(Exception):
                self._upload("failure.txt", content)

        # Upload directory must not have orphaned files
        remaining = os.listdir(self.upload_dir.name)
        self.assertEqual(len(remaining), 0)


if __name__ == "__main__":
    unittest.main()
