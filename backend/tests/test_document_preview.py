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


class DocumentPreviewTests(unittest.TestCase):
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

    def _seed_document(self, owner, filename: str, content: bytes, content_type: str = "text/plain") -> models.Document:
        disk_path = os.path.join(self.upload_dir.name, filename)
        with open(disk_path, "wb") as f:
            f.write(content)
        doc = models.Document(
            filename=filename,
            original_filename=filename,
            file_path=disk_path,
            file_size=len(content),
            content_type=content_type,
            owner_id=owner.id,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def test_preview_png(self):
        png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20
        doc = self._seed_document(self.alice, "test.png", png_content, "image/png")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/png")
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
        self.assertIn("sandbox", res.headers.get("content-security-policy", ""))
        self.assertIn("inline", res.headers.get("content-disposition", ""))

        # Check preview audit log
        logs = self.db.query(models.AuditLog).filter(models.AuditLog.action == "preview").all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].target_id, doc.id)

    def test_preview_jpeg(self):
        jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
        doc = self._seed_document(self.alice, "test.jpg", jpeg_content, "image/jpeg")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/jpeg")

    def test_preview_pdf(self):
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        doc = self._seed_document(self.alice, "test.pdf", pdf_content, "application/pdf")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/pdf")

    def test_preview_plaintext(self):
        txt_content = b"Hello, this is a plain text notes document."
        doc = self._seed_document(self.alice, "notes.txt", txt_content, "text/plain")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/plain", res.headers.get("content-type"))
        self.assertEqual(res.content, txt_content)

    def test_preview_active_html_rejected(self):
        html_content = b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>"
        doc = self._seed_document(self.alice, "malicious.html", html_content, "text/html")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 415)
        self.assertIn("Active content", res.json()["detail"])

        # No preview audit record for failed preflight
        logs = self.db.query(models.AuditLog).filter(models.AuditLog.action == "preview").all()
        self.assertEqual(len(logs), 0)

    def test_preview_svg_rejected(self):
        svg_content = b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>"
        doc = self._seed_document(self.alice, "vector.svg", svg_content, "image/svg+xml")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 415)

    def test_preview_binary_unsupported(self):
        bin_content = b"\x7fELF\x02\x01\x01\x00" + b"\xff" * 20
        doc = self._seed_document(self.alice, "app.bin", bin_content, "application/octet-stream")

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 415)

    def test_preview_foreign_doc_returns_404(self):
        png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 20
        doc = self._seed_document(self.alice, "alice.png", png_content, "image/png")

        # Bob tries to preview Alice's document
        self._as_user(self.bob)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["detail"], "Document not found")

    def test_preview_missing_doc_returns_404(self):
        self._as_user(self.alice)
        res = self.client.get("/documents/99999/preview")
        self.assertEqual(res.status_code, 404)

    def test_preview_missing_file_on_disk_returns_404(self):
        doc = models.Document(
            filename="ghost.png",
            original_filename="ghost.png",
            file_path=os.path.join(self.upload_dir.name, "ghost.png"),
            file_size=100,
            content_type="image/png",
            owner_id=self.alice.id,
        )
        self.db.add(doc)
        self.db.commit()

        self._as_user(self.alice)
        res = self.client.get(f"/documents/{doc.id}/preview")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["detail"], "File not found on server")


if __name__ == "__main__":
    unittest.main()
