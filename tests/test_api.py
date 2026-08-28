import os

import pytest

from appmanager import create_app
from appmanager.database import db
from appmanager.models import InstalledApp, User


@pytest.fixture
def api_client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret",
            "JWT_SECRET": "test-jwt-secret",
            "APPMANAGER_API_KEY": "test-valid-api-key",
        }
    )

    with app.app_context():
        # Create an admin user
        admin = User(email="admin@example.com", name="Admin", role="admin")
        db.session.add(admin)

        # Create a sample app
        sample_app = InstalledApp(
            name="API Test App",
            slug="api-test-app",
            description="Testing REST API",
            source_type="git",
            entry_point="app:app",
            is_active=True,
        )
        db.session.add(sample_app)
        db.session.commit()

        # Dummy directory for health check
        app_dir = tmp_path / "installed_apps" / "api-test-app"
        os.makedirs(app_dir, exist_ok=True)
        with open(app_dir / "app.py", "w") as f:
            f.write(
                "from flask import Flask, jsonify\napp = Flask(__name__)\n@app.route('/health')\ndef h(): return jsonify({'status': 'healthy'})\n"
            )

    return app.test_client()


def test_api_health_endpoint(api_client):
    res = api_client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "healthy"
    assert data["database"]["connected"] is True
    assert data["apps"]["total"] >= 1


def test_api_list_apps_endpoint(api_client):
    res = api_client.get("/api/v1/apps")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert len(data["apps"]) >= 1
    assert data["apps"][0]["slug"] == "api-test-app"


def test_api_get_single_app(api_client):
    res = api_client.get("/api/v1/apps/api-test-app")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["app"]["name"] == "API Test App"


def test_api_get_nonexistent_app(api_client):
    res = api_client.get("/api/v1/apps/nonexistent-app")
    assert res.status_code == 404
    data = res.get_json()
    assert data["success"] is False


def test_api_auth_required_without_key(api_client):
    # Trigger health check requires auth
    res = api_client.post("/api/v1/apps/api-test-app/health-check")
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False


def test_api_trigger_health_check_with_key(api_client):
    headers = {"X-API-Key": "test-valid-api-key"}
    res = api_client.post("/api/v1/apps/api-test-app/health-check", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["app_slug"] == "api-test-app"
    assert data["status"] == "healthy"


def test_api_reload_endpoint(api_client):
    headers = {"X-API-Key": "test-valid-api-key"}
    res = api_client.post("/api/v1/apps/api-test-app/reload", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_api_metrics_endpoint(api_client):
    res = api_client.get("/api/v1/metrics")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "metrics" in data
