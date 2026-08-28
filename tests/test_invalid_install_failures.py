import io
import json
import zipfile

import pytest

from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.cli import install_git_cli, install_zip_cli
from appmanager.database import db
from appmanager.models import User


@pytest.fixture
def app(tmp_path):
    test_db = tmp_path / "test_failures.db"
    installed_apps_dir = tmp_path / "installed_apps"
    installed_apps_dir.mkdir()
    temp_upload_dir = tmp_path / "uploads"
    temp_upload_dir.mkdir()

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{test_db}",
            "INSTALLED_APPS_DIR": str(installed_apps_dir),
            "TEMP_UPLOAD_DIR": str(temp_upload_dir),
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
            "WTF_CSRF_ENABLED": False,
        }
    )

    with app.app_context():
        db.create_all()
        admin_user = User(email="admin@example.com", role="admin", is_active=True)
        db.session.add(admin_user)
        db.session.commit()

    yield app


@pytest.fixture
def auth_client(app):
    client = app.test_client()
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").first()
        token = generate_jwt(admin)
    client.set_cookie(JWT_COOKIE_NAME, token)
    return client


def test_corrupted_zip_fails_precheck(auth_client):
    corrupt_data = io.BytesIO(b"THIS_IS_NOT_A_VALID_ZIP_FILE_CONTENT")
    data = {
        "zip_file": (corrupt_data, "corrupt.zip"),
        "name": "Corrupt App",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json["success"] is False
    assert (
        "Invalid or corrupted ZIP archive" in res_json["error"]
        or "Failed to extract" in res_json["error"]
    )


def test_empty_zip_without_entrypoint_fails_precheck(auth_client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "Just a readme without code")
    bio.seek(0)

    data = {
        "zip_file": (bio, "empty_app.zip"),
        "name": "Empty App",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json["success"] is False
    assert (
        "No valid entrypoint" in res_json["error"] or "Invalid sub-app package" in res_json["error"]
    )


def test_invalid_manifest_syntax_zip_fails_precheck(auth_client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "THIS IS NOT VALID JSON {[[}")
        zf.writestr("app.py", "from flask import Flask\napp = Flask(__name__)\n")
    bio.seek(0)

    data = {
        "zip_file": (bio, "bad_manifest.zip"),
        "name": "Bad Manifest App",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code in (200, 400)


def test_invalid_git_url_fails_precheck(auth_client):
    data = {
        "repo_url": "https://127.0.0.1:59999/nonexistent-repo.git",
        "name": "Invalid Git App",
    }
    resp = auth_client.post("/admin/apps/precheck-git", data=data)
    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json["success"] is False
    assert (
        "Failed to clone Git repository" in res_json["error"]
        or "Git clone failed" in res_json["error"]
    )


def test_unsafe_git_url_rejected(auth_client):
    data = {
        "repo_url": "https://github.com/test/repo.git; rm -rf /",
        "name": "Injected Git URL",
    }
    resp = auth_client.post("/admin/apps/precheck-git", data=data)
    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json["success"] is False
    assert "Invalid or unsafe git repository URL" in res_json["error"]


def test_install_confirm_with_nonexistent_staging_id(auth_client):
    payload = {
        "staging_id": "stage_999999999999",
        "name": "Missing Staged App",
    }
    resp = auth_client.post("/admin/apps/install-confirm", json=payload)
    assert resp.status_code == 400
    res_json = resp.get_json()
    assert res_json["success"] is False
    assert "not found or has expired" in res_json["error"]


def _create_simple_zip():
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"name": "Existing App", "slug": "existing-app", "entry_point": "app:app"}),
        )
        zf.writestr("app.py", "from flask import Flask\napp = Flask(__name__)\n")
    bio.seek(0)
    return bio


def test_install_confirm_duplicate_slug_fails(auth_client, app):
    # 1. Precheck & install first time
    zip1 = _create_simple_zip()
    resp1 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (zip1, "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    assert resp1.status_code == 200
    staging_id_1 = resp1.get_json()["staging_id"]

    confirm_resp1 = auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": staging_id_1}
    )
    assert confirm_resp1.status_code == 200
    assert confirm_resp1.get_json()["success"] is True

    # 2. Precheck second time with same slug
    zip2 = _create_simple_zip()
    resp2 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (zip2, "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    assert resp2.status_code == 200
    staging_id_2 = resp2.get_json()["staging_id"]

    # 3. Confirm second time should fail with duplicate slug error
    confirm_resp2 = auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": staging_id_2, "slug": "existing-app"}
    )
    assert confirm_resp2.status_code == 400
    res_json2 = confirm_resp2.get_json()
    assert res_json2["success"] is False
    assert "already installed" in res_json2["error"]


def _create_zip_with_version(version):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "Existing App",
                    "slug": "existing-app",
                    "version": version,
                    "entry_point": "app:app",
                }
            ),
        )
        zf.writestr("app.py", "from flask import Flask\napp = Flask(__name__)\n")
    bio.seek(0)
    return bio


def test_precheck_reports_slug_conflict_with_versions(auth_client, app):
    # Install v1 first.
    resp1 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("1.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    assert resp1.status_code == 200
    staging_id_1 = resp1.get_json()["staging_id"]
    assert resp1.get_json()["slug_conflict"] is None
    assert auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": staging_id_1}
    ).get_json()["success"] is True

    # Precheck v2 with the same slug -> conflict reported with version diff.
    resp2 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("2.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    assert resp2.status_code == 200
    conflict = resp2.get_json()["slug_conflict"]
    assert conflict is not None
    assert conflict["slug_conflict"] is True
    assert conflict["existing_version"] == "1.0.0"
    assert conflict["new_version"] == "2.0.0"
    assert conflict["versions_differ"] is True


def test_install_confirm_update_replaces_existing_app(auth_client, app):
    # Install v1.
    resp1 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("1.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    staging_id_1 = resp1.get_json()["staging_id"]
    assert auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": staging_id_1}
    ).get_json()["success"] is True

    # Precheck v2 and confirm with conflict_action=update -> replaces, same slug.
    resp2 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("2.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    staging_id_2 = resp2.get_json()["staging_id"]
    confirm2 = auth_client.post(
        "/admin/apps/install-confirm",
        json={"staging_id": staging_id_2, "slug": "existing-app", "conflict_action": "update"},
    )
    assert confirm2.status_code == 200
    res2 = confirm2.get_json()
    assert res2["success"] is True
    assert res2["slug"] == "existing-app"

    # Only one app record remains (updated, not duplicated).
    from appmanager.models import InstalledApp

    with app.app_context():
        apps = InstalledApp.query.filter_by(slug="existing-app").all()
        assert len(apps) == 1


def test_install_confirm_new_slug_installs_separate_app(auth_client, app):
    # Install v1 under existing-app.
    resp1 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("1.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    staging_id_1 = resp1.get_json()["staging_id"]
    assert auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": staging_id_1}
    ).get_json()["success"] is True

    # Precheck again, confirm with a different slug -> installs as a new app.
    resp2 = auth_client.post(
        "/admin/apps/precheck-zip",
        data={"zip_file": (_create_zip_with_version("2.0.0"), "app.zip"), "name": "Existing App"},
        content_type="multipart/form-data",
    )
    staging_id_2 = resp2.get_json()["staging_id"]
    confirm2 = auth_client.post(
        "/admin/apps/install-confirm",
        json={"staging_id": staging_id_2, "slug": "existing-app-v2", "conflict_action": "new_slug"},
    )
    assert confirm2.status_code == 200
    assert confirm2.get_json()["success"] is True
    assert confirm2.get_json()["slug"] == "existing-app-v2"

    from appmanager.models import InstalledApp

    with app.app_context():
        assert len(InstalledApp.query.filter_by(slug="existing-app").all()) == 1
        assert len(InstalledApp.query.filter_by(slug="existing-app-v2").all()) == 1


def test_cli_install_corrupt_zip_fails(app, tmp_path):
    corrupt_file = tmp_path / "bad.zip"
    corrupt_file.write_text("Not a zip file")

    exit_code = install_zip_cli(zip_path=str(corrupt_file), name="Bad App", app=app)
    assert exit_code == 1


def test_cli_install_invalid_git_fails(app):
    exit_code = install_git_cli(
        repo_url="https://127.0.0.1:59999/bad-repo.git", name="Bad Git", app=app
    )
    assert exit_code == 1
