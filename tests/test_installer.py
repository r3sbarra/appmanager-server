import io
import os
import zipfile

import pytest

from appmanager import create_app
from appmanager.admin.app_installer import install_from_zip, uninstall_app
from appmanager.database import db
from appmanager.models import InstalledApp, User


@pytest.fixture
def app():
    app_dir = "/tmp/appmanager_test_apps"
    upload_dir = "/tmp/appmanager_test_uploads"
    os.makedirs(app_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)

    test_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": app_dir,
            "TEMP_UPLOAD_DIR": upload_dir,
            "SECRET_KEY": "test-key",
            "JWT_SECRET": "test-jwt-key",
        }
    )
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


def create_sample_flask_app_zip():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        app_py_content = """from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Hello from Sample Sub-App!"
"""
        zf.writestr("app.py", app_py_content)
    zip_buffer.seek(0)
    return zip_buffer


def test_install_from_zip(app):
    with app.app_context():
        # Create a test user first
        u = User(email="testadmin@example.com", role="admin")
        db.session.add(u)
        db.session.commit()

        zip_buf = create_sample_flask_app_zip()

        class DummyFileStorage:
            def __init__(self, buf, filename):
                self.buf = buf
                self.filename = filename

            def save(self, dst):
                with open(dst, "wb") as f:
                    f.write(self.buf.getvalue())

        dummy_file = DummyFileStorage(zip_buf, "sample_app.zip")

        installed = install_from_zip(dummy_file, name="Sample App", slug="sample-app")
        assert installed.id is not None
        assert installed.slug == "sample-app"
        assert installed.entry_point == "app:app"

        # Verify uninstall
        ok, msg = uninstall_app(installed.id)
        assert ok is True
        assert InstalledApp.query.filter_by(slug="sample-app").first() is None
