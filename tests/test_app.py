import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter

from app import create_app
from extensions import db
from models import Document


def pdf_bytes(pages=1):
    stream = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    stream.seek(0)
    return stream


def png_bytes(color=(115, 87, 255, 128)):
    stream = io.BytesIO()
    Image.new("RGBA", (120, 80), color).save(stream, "PNG")
    stream.seek(0)
    return stream


class DocuFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)

        class TestConfig:
            TESTING = True
            SECRET_KEY = "test-secret"
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'test.db').as_posix()}"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            SQLALCHEMY_ENGINE_OPTIONS = {}
            UPLOAD_FOLDER = str(root / "uploads")
            ALLOWED_EXTENSIONS = {"pdf", "docx", "png", "jpg", "jpeg"}
            MAX_CONTENT_LENGTH = 50 * 1024 * 1024
            CSRF_ENABLED = False
            AUTO_CREATE_DB = True
            SESSION_COOKIE_HTTPONLY = True
            SESSION_COOKIE_SAMESITE = "Lax"
            REMEMBER_COOKIE_HTTPONLY = True
            REMEMBER_COOKIE_SAMESITE = "Lax"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.temp.cleanup()

    def register(self, email="user@example.com"):
        return self.client.post(
            "/register",
            data={"name": "Test User", "email": email, "password": "StrongPass123!", "language": "en"},
        )

    def upload(self, stream, filename):
        return self.client.post(
            "/upload", data={"file": (stream, filename)}, content_type="multipart/form-data"
        )

    def test_complete_document_workflow(self):
        self.assertEqual(self.register().status_code, 302)
        self.assertEqual(self.upload(pdf_bytes(2), "first.pdf").status_code, 302)
        self.assertEqual(self.upload(pdf_bytes(1), "second.pdf").status_code, 302)
        self.assertEqual(self.upload(png_bytes(), "cover.png").status_code, 302)

        with self.app.app_context():
            docs = Document.query.order_by(Document.id).all()
            first_id, second_id, image_id = [doc.id for doc in docs]

        self.assertEqual(
            self.client.post("/tools", data={"action": "merge", "document_ids": [first_id, second_id]}).status_code,
            302,
        )
        self.assertEqual(
            self.client.post("/tools", data={"action": "split", "document_id": first_id, "start": 2, "end": 2}).status_code,
            302,
        )
        self.assertEqual(
            self.client.post("/tools", data={"action": "image", "document_id": image_id}).status_code,
            302,
        )

        with self.app.app_context():
            self.assertEqual(Document.query.count(), 6)
            merged = Document.query.filter_by(original_name="merged.pdf").one()
            split = Document.query.filter(Document.original_name.contains("pages_2-2")).one()
            converted = Document.query.filter_by(original_name="cover.pdf").one()
            upload_root = Path(self.app.config["UPLOAD_FOLDER"])
            self.assertEqual(len(PdfReader(upload_root / merged.stored_name).pages), 3)
            self.assertEqual(len(PdfReader(upload_root / split.stored_name).pages), 1)
            self.assertEqual(len(PdfReader(upload_root / converted.stored_name).pages), 1)

        response = self.client.get("/?q=first")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"first.pdf", response.data)

    def test_rejects_file_with_fake_extension(self):
        self.register()
        response = self.upload(io.BytesIO(b"not a pdf"), "malicious.pdf")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Document.query.count(), 0)
        self.assertEqual(list(Path(self.app.config["UPLOAD_FOLDER"]).iterdir()), [])

    def test_csrf_and_security_headers(self):
        self.app.config["CSRF_ENABLED"] = True
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'csrf_token', response.data)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        rejected = self.client.post(
            "/login", data={"email": "user@example.com", "password": "StrongPass123!"}
        )
        self.assertEqual(rejected.status_code, 400)
        self.app.config["CSRF_ENABLED"] = False

    def test_users_cannot_access_each_others_documents(self):
        self.register("one@example.com")
        self.upload(pdf_bytes(), "private.pdf")
        with self.app.app_context():
            document_id = Document.query.one().id
        self.client.post("/logout")
        self.register("two@example.com")
        self.assertEqual(self.client.get(f"/documents/{document_id}/download").status_code, 404)
        self.assertEqual(self.client.post(f"/documents/{document_id}/delete").status_code, 404)

    def test_profile_favorites_and_trash(self):
        self.register()
        self.upload(pdf_bytes(), "contract.pdf")
        with self.app.app_context():
            document_id = Document.query.one().id

        self.assertEqual(self.client.get("/profile").status_code, 200)
        self.client.post(
            "/profile/details",
            data={"name": "Updated User", "email": "updated@example.com", "language": "kk"},
        )
        self.client.post(
            "/profile/preferences",
            data={"theme": "dark", "compact_mode": "on"},
        )
        self.client.post(f"/documents/{document_id}/favorite")
        self.client.post(
            f"/documents/{document_id}/update",
            data={"name": "signed-contract.pdf", "folder": "Legal", "tags": "signed, client"},
        )
        self.client.post(f"/documents/{document_id}/delete")
        self.assertEqual(self.client.get("/trash").status_code, 200)
        with self.app.app_context():
            document = db.session.get(Document, document_id)
            self.assertTrue(document.is_favorite)
            self.assertIsNotNone(document.deleted_at)
            self.assertEqual(document.folder, "Legal")
            self.assertEqual(document.tags, "signed, client")
        self.client.post(f"/documents/{document_id}/restore")
        with self.app.app_context():
            self.assertIsNone(db.session.get(Document, document_id).deleted_at)

    def test_extended_pdf_tools(self):
        self.register()
        self.upload(pdf_bytes(3), "manual.pdf")
        self.upload(png_bytes(), "one.png")
        self.upload(png_bytes((20, 160, 100, 255)), "two.png")
        with self.app.app_context():
            docs = {doc.original_name: doc.id for doc in Document.query.all()}
        self.assertEqual(self.client.get("/tools?mode=merge").status_code, 200)
        self.client.post(
            "/tools",
            data={"action": "split", "document_ids": docs["manual.pdf"], "ranges": "1,3"},
        )
        self.client.post(
            "/tools",
            data={"action": "rotate", "document_ids": docs["manual.pdf"], "angle": "90"},
        )
        self.client.post(
            "/tools",
            data={"action": "compress", "document_ids": docs["manual.pdf"]},
        )
        self.client.post(
            "/tools",
            data={
                "action": "image",
                "document_ids": [docs["one.png"], docs["two.png"]],
                "page_size": "a4",
                "output_name": "scans",
            },
        )
        self.client.post(
            "/tools",
            data={
                "action": "watermark",
                "document_ids": docs["manual.pdf"],
                "watermark_text": "ҚҰПИЯ",
                "opacity": "55",
                "page_numbers": "on",
            },
        )
        with self.app.app_context():
            names = {doc.original_name for doc in Document.query.all()}
            self.assertIn("manual_pages_1_3.pdf", names)
            self.assertIn("manual_rotated_90.pdf", names)
            self.assertIn("manual_compressed.pdf", names)
            self.assertIn("manual_watermarked.pdf", names)
            scans = Document.query.filter_by(original_name="scans.pdf").one()
            self.assertEqual(scans.page_count, 2)
            marked = Document.query.filter_by(original_name="manual_watermarked.pdf").one()
            self.assertEqual(marked.page_count, 3)

    def test_bulk_document_organization(self):
        self.register()
        self.upload(pdf_bytes(), "one.pdf")
        self.upload(pdf_bytes(2), "two.pdf")
        with self.app.app_context():
            ids = [doc.id for doc in Document.query.order_by(Document.id).all()]
        response = self.client.post(
            "/documents/bulk",
            data={"document_ids": ids, "action": "folder", "folder": "Projects"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual({doc.folder for doc in Document.query.all()}, {"Projects"})
        self.client.post("/documents/bulk", data={"document_ids": ids, "action": "favorite"})
        with self.app.app_context():
            self.assertTrue(all(doc.is_favorite for doc in Document.query.all()))

    def test_admin_can_inspect_and_manage_a_user(self):
        self.register("member@example.com")
        self.client.post("/logout")
        with self.app.app_context():
            from models import User
            from werkzeug.security import generate_password_hash

            admin = User(
                name="Admin",
                email="admin@example.com",
                password_hash=generate_password_hash("AdminPass123!"),
                role="admin",
            )
            db.session.add(admin)
            db.session.commit()
            member_id = User.query.filter_by(email="member@example.com").one().id
        self.client.post("/login", data={"email": "admin@example.com", "password": "AdminPass123!"})
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.assertEqual(self.client.get(f"/admin/users/{member_id}").status_code, 200)
        self.client.post(
            f"/admin/users/{member_id}/settings",
            data={"role": "user", "storage_quota_mb": "256"},
        )
        with self.app.app_context():
            from models import User

            member = db.session.get(User, member_id)
            self.assertFalse(member.is_active)
            self.assertEqual(member.storage_quota, 256 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()

