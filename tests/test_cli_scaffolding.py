import os
import zipfile

from appmanager import create_app
from appmanager.admin.app_installer import validate_subapp_package
from appmanager.cli import export_subapp_cli, new_subapp
from appmanager.database import db
from appmanager.models import InstalledApp


def test_cli_new_subapp_generation(tmp_path):
    target_dir = str(tmp_path / "my_demo_subapp")
    new_subapp(name="My Demo SubApp", slug="demo-subapp", output_dir=target_dir, template="full")

    assert os.path.exists(os.path.join(target_dir, "manifest.json"))
    assert os.path.exists(os.path.join(target_dir, "app.py"))
    assert os.path.exists(os.path.join(target_dir, "requirements.txt"))
    assert os.path.exists(os.path.join(target_dir, "tasks.py"))

    # Validate generated package
    is_valid, errors, manifest = validate_subapp_package(target_dir)
    assert is_valid is True
    assert len(errors) == 0
    assert manifest["name"] == "My Demo SubApp"
    assert manifest["slug"] == "demo-subapp"


def test_cli_validate_invalid_package(tmp_path):
    invalid_dir = str(tmp_path / "invalid_app")
    os.makedirs(invalid_dir, exist_ok=True)
    # Missing manifest and app.py
    is_valid, errors, _ = validate_subapp_package(invalid_dir)
    assert is_valid is False
    assert len(errors) > 0


def test_cli_export_subapp(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    subapp_dir = tmp_path / "installed_apps" / "exportable-app"
    os.makedirs(subapp_dir, exist_ok=True)
    with open(subapp_dir / "app.py", "w") as f:
        f.write("from flask import Flask\napp = Flask(__name__)\n")
    with open(subapp_dir / "manifest.json", "w") as f:
        f.write('{"name": "Exportable", "slug": "exportable-app"}')

    with app.app_context():
        rec = InstalledApp(
            name="Exportable", slug="exportable-app", source_type="zip", is_active=True
        )
        db.session.add(rec)
        db.session.commit()

        zip_target = str(tmp_path / "exported.zip")
        res = export_subapp_cli(slug="exportable-app", output=zip_target, app=app)
        assert res == 0
        assert os.path.exists(zip_target)
        assert zipfile.is_zipfile(zip_target)
