import io
import os
import zipfile

import pytest
from flask import current_app
from werkzeug.datastructures import FileStorage

from appmanager import create_app
from appmanager.admin.app_installer import (
    finalize_zip_replacement,
    stage_zip_replacement,
    update_app_from_git,
)
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.database import db
from appmanager.models import User


@pytest.fixture
def app_with_admin(tmp_path):
    installed_dir = tmp_path / "installed_apps"
    installed_dir.mkdir()

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": str(installed_dir),
            "SECRET_KEY": "test-secret-key-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-32-bytes-long-key-for-testing!",
        }
    )

    with app.app_context():
        db.create_all()
        admin = User(email="admin@domain.com", role="admin")
        db.session.add(admin)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def _make_zip_storage(
    name: str, slug: str, version: str = "1.0.0", message: str = "Hello v1"
) -> FileStorage:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            f'{{"name": "{name}", "slug": "{slug}", "version": "{version}", "entry_point": "app:app"}}',
        )
        zf.writestr(
            "app.py",
            f'from flask import Flask\napp = Flask(__name__)\n@app.route("/")\ndef i(): return "{message}"',
        )
        zf.writestr("requirements.txt", "# clean requirements\n")
    buf.seek(0)
    return FileStorage(stream=buf, filename=f"{slug}.zip", content_type="application/zip")


def test_zip_app_replacement_flow(app_with_admin):
    with app_with_admin.app_context():
        # 1. Install original version 1.0.0
        zip_v1 = _make_zip_storage("My Tool", "my-tool", version="1.0.0", message="Version 1.0")
        from appmanager.admin.app_installer import install_from_zip

        app_rec = install_from_zip(zip_v1, name="My Tool", slug="my-tool")
        assert app_rec.id is not None
        assert app_rec.slug == "my-tool"

        app_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], "my-tool")
        with open(os.path.join(app_dir, "app.py"), "r") as f:
            assert "Version 1.0" in f.read()

        # 2. Stage replacement version 2.0.0
        zip_v2 = _make_zip_storage(
            "My Tool", "my-tool", version="2.0.0", message="Version 2.0 Upgraded!"
        )
        staging_id, scan_report, manifest, dep_report = stage_zip_replacement(app_rec.id, zip_v2)
        assert scan_report.is_safe is True
        assert dep_report.is_safe is True
        assert manifest["version"] == "2.0.0"

        # 3. Finalize replacement
        upgraded = finalize_zip_replacement(staging_id)
        assert upgraded.id == app_rec.id
        assert upgraded.slug == "my-tool"

        # Verify upgraded files on disk
        with open(os.path.join(app_dir, "app.py"), "r") as f:
            assert "Version 2.0 Upgraded!" in f.read()


def test_update_app_from_git_validation(app_with_admin):
    with app_with_admin.app_context():
        # App installed via ZIP should reject git update
        zip_v1 = _make_zip_storage("Zip App", "zip-app")
        from appmanager.admin.app_installer import install_from_zip

        app_rec = install_from_zip(zip_v1, name="Zip App", slug="zip-app")

        ok, msg, details = update_app_from_git(app_rec.id)
        assert ok is False
        assert "not installed via Git" in msg


def test_admin_routes_update_and_dependencies(app_with_admin):
    client = app_with_admin.test_client()

    with app_with_admin.app_context():
        admin = User.query.filter_by(email="admin@domain.com").first()
        token = generate_jwt(admin)

        zip_v1 = _make_zip_storage("API Test App", "api-test-app")
        from appmanager.admin.app_installer import install_from_zip

        app_rec = install_from_zip(zip_v1, name="API Test App", slug="api-test-app")
        app_id = app_rec.id

    client.set_cookie(JWT_COOKIE_NAME, token)

    # 1. Test GET /admin/apps/<id>/dependencies
    res = client.get(f"/admin/apps/{app_id}/dependencies")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "dependencies" in data
    assert data["slug"] == "api-test-app"

    # 2. Test POST /admin/apps/<id>/update-git for non-git app
    res = client.post(f"/admin/apps/{app_id}/update-git", json={})
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
