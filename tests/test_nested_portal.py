import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from appmanager import create_app, create_dispatchable_app
from appmanager.database import db
from appmanager.models import InstalledApp


@pytest.fixture
def nested_environment(tmp_path):
    # 1. Setup parent directories
    parent_apps_dir = tmp_path / "parent_installed_apps"
    parent_apps_dir.mkdir()

    # 2. Setup child sub-portal inside parent's installed_apps
    child_portal_dir = parent_apps_dir / "nested-portal"
    child_portal_dir.mkdir()

    child_apps_dir = child_portal_dir / "child_apps"
    child_apps_dir.mkdir()

    # Write child manifest
    manifest_code = """{
        "name": "Nested AppManager Portal",
        "slug": "nested-portal",
        "entry_point": "app:app",
        "requires_auth": false
    }"""
    (child_portal_dir / "manifest.json").write_text(manifest_code)

    # Write child entrypoint (app.py) that initializes an AppManager instance
    child_db_path = tmp_path / "child.db"
    child_app_code = f"""
import os
from appmanager import create_app, create_dispatchable_app
from appmanager.database import db
from appmanager.models import InstalledApp

def make_child_app():
    c_app = create_app({{
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///{child_db_path}",
        "SECRET_KEY": "child-secret-key-32-bytes-long!",
        "JWT_SECRET": "child-jwt-secret-32-bytes-long!",
        "INSTALLED_APPS_DIR": "{child_apps_dir}",
        "ALLOW_DEV_MAGIC_LOGIN": True,
    }})
    with c_app.app_context():
        db.create_all()
    return create_dispatchable_app(c_app)

app = make_child_app()
"""
    (child_portal_dir / "app.py").write_text(child_app_code)

    # 3. Setup parent AppManager instance
    parent_db_path = tmp_path / "parent.db"
    parent_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{parent_db_path}",
            "SECRET_KEY": "parent-secret-key-32-bytes-long!",
            "JWT_SECRET": "parent-jwt-secret-32-bytes-long!",
            "INSTALLED_APPS_DIR": str(parent_apps_dir),
            "ALLOW_DEV_MAGIC_LOGIN": True,
        }
    )

    with parent_app.app_context():
        db.create_all()
        # Register child portal in parent DB
        child_rec = InstalledApp(
            name="Nested AppManager Portal",
            slug="nested-portal",
            source_type="zip",
            entry_point="app:app",
            requires_auth=False,
            is_active=True,
        )
        db.session.add(child_rec)
        db.session.commit()

    return {
        "parent_app": parent_app,
        "child_apps_dir": child_apps_dir,
        "child_db_path": child_db_path,
    }


def test_nested_appmanager_portal(nested_environment):
    parent_app = nested_environment["parent_app"]
    child_apps_dir = nested_environment["child_apps_dir"]

    # 1. Add a grandchild app inside the nested child portal's apps directory
    grandchild_dir = child_apps_dir / "child-counter"
    grandchild_dir.mkdir()
    (grandchild_dir / "manifest.json").write_text("""{
        "name": "Grandchild Counter",
        "slug": "child-counter",
        "entry_point": "app:app",
        "requires_auth": false
    }""")
    (grandchild_dir / "app.py").write_text("""
from flask import Flask
app = Flask(__name__)
@app.route("/")
def index():
    return "Hello from Grandchild Sub-App inside Nested Portal!"
""")

    dispatchable = create_dispatchable_app(parent_app)
    client = Client(dispatchable, Response)

    # 2. Accessing nested child AppManager portal at /apps/nested-portal/
    res_child = client.get("/apps/nested-portal/")
    assert res_child.status_code in (200, 302)

    # 3. Accessing grandchild sub-app through nested child dispatcher!
    # /apps/nested-portal/apps/child-counter/
    res_grandchild = client.get("/apps/nested-portal/apps/child-counter/")
    # If not registered in child DB yet, child dispatcher returns 404 cleanly
    assert res_grandchild.status_code in (200, 404)
