import io
import json
import os
import zipfile

import pytest

from appmanager import create_app
from appmanager.admin.app_installer import (
    _staged_installations,
)
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.database import db
from appmanager.models import InstalledApp, User


@pytest.fixture
def app(tmp_path):
    test_db = tmp_path / "test_precheck.db"
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
            "SECRET_KEY": "test-secret-key-precheck-at-least-32-chars!",
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
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(client, app):
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").first()
        token = generate_jwt(admin)
    client.set_cookie(JWT_COOKIE_NAME, token)
    return client


def _create_test_zip(is_malicious: bool = False) -> io.BytesIO:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {"name": "Test Precheck App", "slug": "test-precheck-app", "entry_point": "app:app"}
            ),
        )
        if is_malicious:
            zf.writestr(
                "app.py",
                (
                    "import os\n"
                    "from flask import Flask\n"
                    "app = Flask(__name__)\n"
                    "os.system('rm -rf /tmp/data')\n"
                ),
            )
        else:
            zf.writestr(
                "app.py",
                (
                    "from flask import Flask\n"
                    "app = Flask(__name__)\n"
                    "@app.route('/')\n"
                    "def hello():\n"
                    "    return 'Hello World'\n"
                ),
            )
        zf.writestr("requirements.txt", "flask>=3.0.0\n")
    bio.seek(0)
    return bio


def test_precheck_zip_endpoint_clean_app(auth_client):
    zip_bytes = _create_test_zip(is_malicious=False)
    data = {
        "zip_file": (zip_bytes, "test_app.zip"),
        "name": "Test Precheck App",
        "slug": "test-precheck-app",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    res_json = resp.get_json()
    assert res_json["success"] is True
    assert "staging_id" in res_json
    assert res_json["report"]["is_safe"] is True
    assert res_json["report"]["risk_level"] == "CLEAN"
    assert len(res_json["report"]["findings"]) == 0

    staging_id = res_json["staging_id"]
    # Confirm installation
    confirm_resp = auth_client.post("/admin/apps/install-confirm", json={"staging_id": staging_id})
    assert confirm_resp.status_code == 200
    confirm_json = confirm_resp.get_json()
    assert confirm_json["success"] is True
    assert confirm_json["slug"] == "test-precheck-app"


def test_precheck_zip_endpoint_malicious_app_detection_and_cancel(auth_client):
    zip_bytes = _create_test_zip(is_malicious=True)
    data = {
        "zip_file": (zip_bytes, "malicious_app.zip"),
        "name": "Malicious App",
        "slug": "malicious-app",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    res_json = resp.get_json()
    assert res_json["success"] is True
    assert res_json["report"]["is_safe"] is False
    assert res_json["report"]["risk_level"] == "CRITICAL"
    assert any(f["category"] == "Command Injection" for f in res_json["report"]["findings"])

    staging_id = res_json["staging_id"]
    # Cancel installation
    cancel_resp = auth_client.post("/admin/apps/cancel-staged", json={"staging_id": staging_id})
    assert cancel_resp.status_code == 200
    assert cancel_resp.get_json()["success"] is True
    assert staging_id not in _staged_installations


def test_cli_install_zip_with_precheck(app, tmp_path, monkeypatch):
    zip_bytes = _create_test_zip(is_malicious=False)
    zip_path = os.path.join(tmp_path, "sample_cli.zip")
    with open(zip_path, "wb") as f:
        f.write(zip_bytes.getvalue())

    from appmanager.cli import install_zip_cli

    # Test with user confirmation 'y'
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    exit_code = install_zip_cli(
        zip_path=zip_path, name="Sample CLI App", slug="sample-cli-app", app=app
    )
    assert exit_code == 0

    with app.app_context():
        installed = InstalledApp.query.filter_by(slug="sample-cli-app").first()
        assert installed is not None
        assert installed.name == "Sample CLI App"


def test_cli_install_zip_cancelled_by_user(app, tmp_path, monkeypatch):
    zip_bytes = _create_test_zip(is_malicious=False)
    zip_path = os.path.join(tmp_path, "sample_cancel.zip")
    with open(zip_path, "wb") as f:
        f.write(zip_bytes.getvalue())

    from appmanager.cli import install_zip_cli

    # Test with user refusal 'n'
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    exit_code = install_zip_cli(
        zip_path=zip_path, name="Sample Cancel App", slug="sample-cancel-app", app=app
    )
    assert exit_code == 1

    with app.app_context():
        installed = InstalledApp.query.filter_by(slug="sample-cancel-app").first()
        assert installed is None


def test_macos_zip_with_nested_folder_and_resource_forks(auth_client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        # Simulate macOS folder archive with __MACOSX and ._ files
        zf.writestr("__MACOSX/._music-player", b"\x00\x05\x16\x07")
        zf.writestr("__MACOSX/music-player/._app.py", b"\x00\x05\x16\x07")
        zf.writestr("music-player/.DS_Store", b"\x00\x00\x00\x01")
        zf.writestr(
            "music-player/manifest.json",
            json.dumps({"name": "Music Player", "slug": "music-player", "entry_point": "app:app"}),
        )
        zf.writestr(
            "music-player/app.py",
            (
                "from flask import Flask\n"
                "app = Flask(__name__)\n"
                "@app.route('/')\n"
                "def home():\n"
                "    return 'Music'\n"
            ),
        )
        zf.writestr("music-player/requirements.txt", "flask\n")
    bio.seek(0)

    data = {
        "zip_file": (bio, "music-player.zip"),
        "name": "Music Player",
        "slug": "music-player",
    }
    resp = auth_client.post(
        "/admin/apps/precheck-zip",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    res_json = resp.get_json()
    assert res_json["success"] is True
    assert res_json["report"]["is_safe"] is True
    assert res_json["report"]["risk_level"] == "CLEAN"
    assert res_json["slug"] == "music-player"

    # Confirm installation
    confirm_resp = auth_client.post(
        "/admin/apps/install-confirm", json={"staging_id": res_json["staging_id"]}
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.get_json()["success"] is True
