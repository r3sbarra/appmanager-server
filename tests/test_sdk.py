import pytest
from flask import Flask, jsonify

from appmanager import create_app
from appmanager.database import db
from appmanager.models import InstalledApp, User
from appmanager.sdk import AppManagerClient


@pytest.fixture
def test_app():
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
        user = User(email="sdk_user@example.com", role="admin")
        app_rec = InstalledApp(
            name="SDK Test App", slug="sdk-test", source_type="zip", is_active=True
        )
        app_rec.set_settings({"api_key": "secret_12345", "timeout": 30})
        db.session.add_all([user, app_rec])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_sdk_get_current_user_from_headers():
    client = AppManagerClient("sdk-test")
    headers = {
        "X-AppManager-User-Id": "10",
        "X-AppManager-User-Email": "dev@example.com",
        "X-AppManager-User-Role": "admin",
    }
    user = client.get_current_user(headers)
    assert user is not None
    assert user["id"] == 10
    assert user["email"] == "dev@example.com"
    assert user["role"] == "admin"
    assert user["is_admin"] is True


def test_sdk_get_setting(test_app):
    with test_app.app_context():
        client = AppManagerClient("sdk-test")
        api_key = client.get_setting("api_key")
        timeout = client.get_setting("timeout")
        missing = client.get_setting("non_existent", default="fallback")

        assert api_key == "secret_12345"
        assert timeout == 30
        assert missing == "fallback"


def test_sdk_extension_data_storage(test_app):
    with test_app.app_context():
        client = AppManagerClient("sdk-test")
        client.set_data("user_pref", 10, {"theme": "dark", "font_size": 14})

        data = client.get_data("user_pref", 10)
        assert data is not None
        assert data["theme"] == "dark"
        assert data["font_size"] == 14


def test_sdk_telemetry_reporting(test_app):
    with test_app.app_context():
        client = AppManagerClient("sdk-test")
        res1 = client.report_event("user_signup", {"source": "google"})
        res2 = client.report_metric("latency", 45.2, unit="ms")
        assert res1 is True
        assert res2 is True


def test_sdk_require_auth_decorator():
    subapp = Flask("subapp_test")
    client = AppManagerClient("subapp_test")

    @subapp.route("/protected")
    @client.require_auth(role="admin")
    def protected_view():
        return jsonify({"status": "ok"})

    test_client = subapp.test_client()

    # Unauthenticated
    res_unauth = test_client.get("/protected", headers={"Accept": "application/json"})
    assert res_unauth.status_code == 401

    # Regular user (role mismatch)
    res_forbidden = test_client.get(
        "/protected",
        headers={
            "Accept": "application/json",
            "X-AppManager-User-Id": "2",
            "X-AppManager-User-Email": "user@example.com",
            "X-AppManager-User-Role": "user",
        },
    )
    assert res_forbidden.status_code == 403

    # Admin user
    res_ok = test_client.get(
        "/protected",
        headers={
            "Accept": "application/json",
            "X-AppManager-User-Id": "1",
            "X-AppManager-User-Email": "admin@example.com",
            "X-AppManager-User-Role": "admin",
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.get_json()["status"] == "ok"
