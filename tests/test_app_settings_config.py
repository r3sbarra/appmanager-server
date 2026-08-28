import json

import pytest

from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.bridge import get_app_settings
from appmanager.database import db
from appmanager.models import InstalledApp, User


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
            "SECRET_KEY": "test-secret-key-32-bytes-minimum-length",
            "JWT_SECRET": "test-jwt-secret-32-bytes-minimum-length",
        }
    )
    with app.app_context():
        db.create_all()
        admin = User(email="admin_cfg@example.com", role="admin")
        app_rec = InstalledApp(
            name="Config App", slug="config-app", source_type="zip", is_active=True
        )
        db.session.add_all([admin, app_rec])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_admin_update_app_settings_route(app):
    client = app.test_client()
    with app.app_context():
        admin = User.query.filter_by(email="admin_cfg@example.com").first()
        app_rec = InstalledApp.query.filter_by(slug="config-app").first()

        client.set_cookie(JWT_COOKIE_NAME, generate_jwt(admin))

        # Update settings via POST
        new_settings = {"theme": "dark", "max_items": 50, "enable_notifications": True}
        res = client.post(
            f"/admin/apps/{app_rec.id}/settings",
            data={"settings_json": json.dumps(new_settings)},
            follow_redirects=True,
        )
        assert res.status_code == 200

        # Verify in DB
        db_app = db.session.get(InstalledApp, app_rec.id)
        assert db_app.get_settings() == new_settings

        # Verify via bridge helper
        bridge_settings = get_app_settings("config-app")
        assert bridge_settings["max_items"] == 50
        assert bridge_settings["theme"] == "dark"


def test_admin_update_invalid_json_settings(app):
    client = app.test_client()
    with app.app_context():
        admin = User.query.filter_by(email="admin_cfg@example.com").first()
        app_rec = InstalledApp.query.filter_by(slug="config-app").first()

        client.set_cookie(JWT_COOKIE_NAME, generate_jwt(admin))

        res = client.post(
            f"/admin/apps/{app_rec.id}/settings",
            data={"settings_json": "invalid-json-{here"},
            follow_redirects=True,
        )
        assert res.status_code == 200
        assert b"Invalid JSON format" in res.data
