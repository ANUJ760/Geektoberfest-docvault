import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, models
from app.database import Base, get_db
from app.routes import folders


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([
            models.User(username="alice", email="alice@example.com", hashed_password="unused"),
            models.User(username="bob", email="bob@example.com", hashed_password="unused"),
        ])
        self.db.commit()
        self.alice, self.bob = self.db.query(models.User).order_by(models.User.id).all()

        def test_db():
            yield self.db

        app = FastAPI()
        app.include_router(folders.router, prefix="/folders")
        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        self.client.headers["Authorization"] = self.authorization(self.alice)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def authorization(self, user):
        return "Bearer " + auth.create_access_token({"sub": user.username})

    def create(self, name="Reports"):
        response = self.client.post("/folders/", json={"name": name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_trims_name(self):
        self.assertEqual(self.create("  Reports\t")["name"], "Reports")

    def test_create_rejects_invalid_names(self):
        for name in ("", " \t\n", None, 123):
            with self.subTest(name=name):
                response = self.client.post("/folders/", json={"name": name})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.query(models.Folder).count(), 0)

    def test_create_rejects_duplicate_after_trimming(self):
        self.create()
        response = self.client.post("/folders/", json={"name": " Reports "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Folder already exists")

    def test_different_users_can_use_same_name(self):
        self.create()
        self.client.headers["Authorization"] = self.authorization(self.bob)
        self.create()
        other = self.create("Other")
        response = self.client.patch(f"/folders/{other['id']}", json={"name": "Archive"})
        self.assertEqual(response.status_code, 200)
        self.client.headers["Authorization"] = self.authorization(self.alice)
        own = self.client.get("/folders/").json()[0]
        response = self.client.patch(f"/folders/{own['id']}", json={"name": "Archive"})
        self.assertEqual(response.status_code, 200)

    def test_rename_preserves_id_owner_and_documents(self):
        folder = self.create()
        document = models.Document(
            filename="test.txt", original_filename="test.txt", file_path="unused",
            file_size=1, content_type="text/plain", folder_id=folder["id"], owner_id=self.alice.id
        )
        self.db.add(document)
        self.db.commit()
        response = self.client.patch(f"/folders/{folder['id']}", json={"name": " Archive "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {**folder, "name": "Archive"})
        self.db.refresh(document)
        self.assertEqual(document.folder_id, folder["id"])
        self.assertEqual(self.client.get("/folders/").json()[0]["name"], "Archive")

    def test_rename_to_same_name_is_allowed(self):
        folder = self.create()
        response = self.client.patch(f"/folders/{folder['id']}", json={"name": " Reports "})
        self.assertEqual(response.status_code, 200)

    def test_rename_rejects_invalid_names_without_changing_folder(self):
        folder = self.create()
        for name in ("", " \t\n", None, 123):
            with self.subTest(name=name):
                response = self.client.patch(f"/folders/{folder['id']}", json={"name": name})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/folders/").json()[0]["name"], "Reports")

    def test_rename_rejects_duplicate_without_changing_folder(self):
        self.create()
        folder = self.create("Other")
        response = self.client.patch(f"/folders/{folder['id']}", json={"name": " Reports "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.get(models.Folder, folder["id"]).name, "Other")

    def test_cannot_rename_missing_or_other_users_folder(self):
        folder = self.create()
        self.client.headers["Authorization"] = self.authorization(self.bob)
        for folder_id in (folder["id"], 999):
            with self.subTest(folder_id=folder_id):
                response = self.client.patch(f"/folders/{folder_id}", json={"name": "Changed"})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.db.get(models.Folder, folder["id"]).name, "Reports")

    def test_mutations_require_authentication(self):
        folder = self.create()
        self.client.headers.pop("Authorization")
        self.assertEqual(self.client.post("/folders/", json={"name": "New"}).status_code, 401)
        self.assertEqual(self.client.patch(f"/folders/{folder['id']}", json={"name": "New"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
