import zipfile

import pytest

from appmanager import create_app
from appmanager.admin.app_installer import validate_subapp_package
from appmanager.database import db
from appmanager.sdk import AdminSection, AppManagerClient, AppManifest, ScheduledTask, Setting


@pytest.fixture
def app():
    test_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key-32-bytes-minimum-length",
            "JWT_SECRET": "test-jwt-secret-32-bytes-minimum-length",
        }
    )
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


def test_appmanager_sdk_imports():
    manifest = AppManifest(
        name="Telemetry Pipeline",
        slug="telemetry-pipeline",
        version="1.0.0",
        settings=[Setting(key="buffer_size", type="integer", default=100)],
        admin_sections=[AdminSection(id="metrics", label="Metrics", blueprint="metrics:bp")],
        scheduled_tasks=[
            ScheduledTask(name="flush_buffer", entry_point="tasks:flush", frequency="hourly")
        ],
    )

    data = manifest.to_dict()
    assert data["name"] == "Telemetry Pipeline"
    assert data["slug"] == "telemetry-pipeline"
    assert data["settings_schema"][0]["key"] == "buffer_size"
    assert data["settings"]["buffer_size"]["default"] == 100
    assert data["admin_sections"][0]["id"] == "metrics"
    assert data["scheduled_tasks"][0]["name"] == "flush_buffer"


def test_subapp_validation_with_python_defined_manifest(tmp_path):
    subapp_dir = tmp_path / "my_py_subapp"
    subapp_dir.mkdir()

    # Create app.py with AppManifest but NO manifest.json
    app_py = subapp_dir / "app.py"
    app_py.write_text("""
from flask import Flask
from appmanager_sdk import AppManifest, Setting

app = Flask(__name__)

manifest = AppManifest(
    name="Python Native SubApp",
    slug="py-native-subapp",
    version="1.0.0",
    entry_point="app:app",
    health_check_path="/health",
    settings=[
        Setting(key="timeout_sec", type="integer", default=30)
    ]
)

@app.route('/health')
def health():
    return {"status": "ok"}
""")

    # Validate directory
    is_valid, errors, manifest_data = validate_subapp_package(str(subapp_dir))
    assert is_valid is True
    assert errors == []
    assert manifest_data["name"] == "Python Native SubApp"
    assert manifest_data["slug"] == "py-native-subapp"
    assert manifest_data["settings_schema"][0]["key"] == "timeout_sec"


def test_subapp_zip_validation_with_python_manifest(tmp_path):
    # Create zip file containing only app.py
    zip_path = tmp_path / "subapp.zip"
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        zf.writestr(
            "app.py",
            """
from flask import Flask
from appmanager_sdk import AppManifest

app = Flask(__name__)
manifest = AppManifest(name="Zipped Python App", slug="zipped-python-app", entry_point="app:app")
""",
        )

    is_valid, errors, manifest_data = validate_subapp_package(str(zip_path))
    assert is_valid is True
    assert errors == []
    assert manifest_data["name"] == "Zipped Python App"
    assert manifest_data["slug"] == "zipped-python-app"


def test_cli_generate_manifest_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from appmanager.cli import main as appmanager_main

    app_py = tmp_path / "app.py"
    app_py.write_text("""
from appmanager_sdk import AppManifest
manifest = AppManifest(name="CLI Test App", slug="cli-test-app")
""")

    ret = appmanager_main(["generate-manifest", str(app_py), "--out", "manifest.json"])
    assert ret == 0
    assert (tmp_path / "manifest.json").exists()


def test_sdk_client_delegation(app):
    with app.app_context():
        client = AppManagerClient("test-delegation")
        assert client.app_slug == "test-delegation"

        headers = {
            "X-AppManager-User-Id": "5",
            "X-AppManager-User-Email": "dev@example.com",
            "X-AppManager-User-Role": "admin",
        }
        user = client.get_current_user(headers)
        assert user["id"] == 5
        assert user["email"] == "dev@example.com"
        assert user["is_admin"] is True
