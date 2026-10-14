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
from app.routes import documents, folders
from app.quota import (
    DEFAULT_STORAGE_QUOTA_BYTES,
    get_configured_quota_bytes,
    validate_quota_configuration,
    get_user_storage_usage,
    check_and_reserve_quota,
)


def _build_app(db_session):
    def test_db():
        yield db_session

    app = FastAPI()
    app.include_router(documents.router, prefix="/documents")
    app.include_router(folders.router, prefix="/folders")
    app.dependency_overrides[get_db] = test_db
    return app


class StorageQuotaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        self.db.add_all([
            models.User(username="alice", email="alice@example.com", hashed_password="pw"),
            models.User(username="bob", email="bob@example.com", hashed_password="pw"),
        ])
        self.db.commit()
        self.alice, self.bob = self.db.query(models.User).order_by(models.User.id).all()

        self.upload_dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(documents, "UPLOAD_DIR", self.upload_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.upload_dir.cleanup)
        self.addCleanup(self.db.close)

        self.app = _build_app(self.db)
        self.client = TestClient(self.app)

    def _as_user(self, user):
        self.app.dependency_overrides[auth.get_current_user] = lambda: user

    def _upload(self, filename: str, content: bytes, folder_id: int | None = None):
        data = {}
        if folder_id is not None:
            data["folder_id"] = str(folder_id)
        return self.client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(content), "text/plain")},
            data=data,
        )

    def test_default_quota_and_startup_validation(self):
        # Default quota is 100 MiB
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_configured_quota_bytes(), DEFAULT_STORAGE_QUOTA_BYTES)
            self.assertEqual(validate_quota_configuration(), DEFAULT_STORAGE_QUOTA_BYTES)

        # Custom valid quota
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": "52428800"}):
            self.assertEqual(get_configured_quota_bytes(), 52428800)
            self.assertEqual(validate_quota_configuration(), 52428800)

        # Invalid non-positive quota
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": "0"}):
            with self.assertRaises(ValueError):
                validate_quota_configuration()

        # Invalid string
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": "invalid"}):
            with self.assertRaises(ValueError):
                validate_quota_configuration()

    def test_storage_usage_endpoint(self):
        self._as_user(self.alice)
        res = self.client.get("/documents/storage-usage")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["used_bytes"], 0)
        self.assertEqual(data["quota_bytes"], DEFAULT_STORAGE_QUOTA_BYTES)
        self.assertEqual(data["remaining_bytes"], DEFAULT_STORAGE_QUOTA_BYTES)
        self.assertEqual(data["used_percentage"], 0.0)

    def test_quota_calculation_scoped_to_owner(self):
        self._as_user(self.alice)
        self._upload("alice.txt", b"A" * 1500)

        self._as_user(self.bob)
        self._upload("bob.txt", b"B" * 2500)

        # Alice's usage reflects only Alice's document
        self._as_user(self.alice)
        res_alice = self.client.get("/documents/storage-usage").json()
        self.assertEqual(res_alice["used_bytes"], 1500)

        # Bob's usage reflects only Bob's document
        self._as_user(self.bob)
        res_bob = self.client.get("/documents/storage-usage").json()
        self.assertEqual(res_bob["used_bytes"], 2500)

    def test_upload_at_exact_boundary(self):
        quota = 5000
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": str(quota)}):
            self._as_user(self.alice)
            res = self._upload("boundary.txt", b"X" * quota)
            self.assertEqual(res.status_code, 200)

            usage = self.client.get("/documents/storage-usage").json()
            self.assertEqual(usage["used_bytes"], quota)
            self.assertEqual(usage["remaining_bytes"], 0)
            self.assertEqual(usage["used_percentage"], 100.0)

    def test_upload_exceeding_quota_rejected_413(self):
        quota = 5000
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": str(quota)}):
            self._as_user(self.alice)
            # Upload 3000 bytes (succeeds)
            res1 = self._upload("part1.txt", b"X" * 3000)
            self.assertEqual(res1.status_code, 200)

            # Upload 2001 bytes (exceeds quota by 1 byte)
            files_before = set(os.listdir(self.upload_dir.name))
            res2 = self._upload("part2.txt", b"Y" * 2001)
            self.assertEqual(res2.status_code, 413)
            self.assertIn("Storage quota exceeded", res2.json()["detail"])
            self.assertIn("Allowed quota", res2.json()["detail"])

            # Verify no orphaned file is left on disk
            files_after = set(os.listdir(self.upload_dir.name))
            self.assertEqual(files_before, files_after)

            # Usage remains 3000 bytes
            usage = self.client.get("/documents/storage-usage").json()
            self.assertEqual(usage["used_bytes"], 3000)

    def test_10mb_per_file_limit_preserved(self):
        self._as_user(self.alice)
        # 10MB + 1 byte
        content = b"X" * (10 * 1024 * 1024 + 1)
        res = self._upload("oversize.txt", content)
        self.assertEqual(res.status_code, 400)
        self.assertIn("File too large. Max size is 10MB", res.json()["detail"])

    def test_download_and_delete_allowed_when_over_quota(self):
        # Pre-seed document putting user over a lowered quota
        self._as_user(self.alice)
        res = self._upload("existing.txt", b"data" * 1000)
        self.assertEqual(res.status_code, 200)
        doc_id = res.json()["id"]

        # Lower quota below existing document size
        with mock.patch.dict(os.environ, {"USER_STORAGE_QUOTA_BYTES": "1000"}):
            # User can still download
            download_res = self.client.get(f"/documents/{doc_id}/download")
            self.assertEqual(download_res.status_code, 200)
            self.assertEqual(download_res.content, b"data" * 1000)

            # User can delete document to reclaim quota
            del_res = self.client.delete(f"/documents/{doc_id}")
            self.assertEqual(del_res.status_code, 204)

            # Usage is now 0
            usage = self.client.get("/documents/storage-usage").json()
            self.assertEqual(usage["used_bytes"], 0)

    def test_delete_folder_reclaims_quota(self):
        self._as_user(self.alice)
        # Create folder
        folder = models.Folder(name="Projects", owner_id=self.alice.id)
        self.db.add(folder)
        self.db.commit()

        # Upload document in folder
        res = self._upload("project_doc.txt", b"Z" * 4000, folder_id=folder.id)
        self.assertEqual(res.status_code, 200)

        usage_before = self.client.get("/documents/storage-usage").json()
        self.assertEqual(usage_before["used_bytes"], 4000)

        # Delete folder
        del_res = self.client.delete(f"/folders/{folder.id}")
        self.assertEqual(del_res.status_code, 204)

        # Quota usage is reclaimed
        usage_after = self.client.get("/documents/storage-usage").json()
        self.assertEqual(usage_after["used_bytes"], 0)

    def test_safe_handling_of_legacy_null_file_sizes(self):
        # Insert a document with file_size=None
        legacy_doc = models.Document(
            filename="legacy.txt",
            original_filename="legacy.txt",
            file_path="/tmp/fake",
            file_size=None,
            content_type="text/plain",
            owner_id=self.alice.id,
        )
        self.db.add(legacy_doc)
        self.db.commit()

        usage = get_user_storage_usage(self.db, self.alice.id)
        self.assertEqual(usage, 0)


if __name__ == "__main__":
    unittest.main()
