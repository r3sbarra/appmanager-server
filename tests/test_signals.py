import os

import pytest

from appmanager import create_app
from appmanager.admin.app_installer import uninstall_app
from appmanager.bridge import report_event
from appmanager.database import db
from appmanager.health import check_app_health
from appmanager.models import InstalledApp
from appmanager.signals import (
    health_check_completed,
    subapp_uninstalled,
    telemetry_received,
)


@pytest.fixture
def app_instance(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret",
            "JWT_SECRET": "test-jwt-secret",
        }
    )
    return app


def test_telemetry_signal(app_instance):
    received_events = []

    def handle_telemetry(sender, **kwargs):
        received_events.append(kwargs)

    telemetry_received.connect(handle_telemetry)

    with app_instance.app_context():
        report_event("test-subapp", "user_signup", {"source": "google"})

    assert len(received_events) >= 1
    assert received_events[-1]["app_slug"] == "test-subapp"
    assert received_events[-1]["event_type"] == "user_signup"
    assert received_events[-1]["data"]["source"] == "google"

    telemetry_received.disconnect(handle_telemetry)


def test_health_check_signal(app_instance, tmp_path):
    received_health = []

    def handle_health(sender, **kwargs):
        received_health.append(kwargs)

    health_check_completed.connect(handle_health)

    with app_instance.app_context():
        # Setup dummy app folder
        app_dir = tmp_path / "installed_apps" / "signal-app"
        os.makedirs(app_dir, exist_ok=True)
        with open(app_dir / "app.py", "w") as f:
            f.write(
                "from flask import Flask, jsonify\napp = Flask(__name__)\n@app.route('/health')\ndef h(): return jsonify({'status': 'healthy'})\n"
            )

        app_record = InstalledApp(
            name="Signal App",
            slug="signal-app",
            source_type="git",
            entry_point="app:app",
            is_active=True,
        )
        db.session.add(app_record)
        db.session.commit()

        check_app_health(app_record)

    assert len(received_health) >= 1
    assert received_health[-1]["app_slug"] == "signal-app"
    assert received_health[-1]["status"] == "healthy"

    health_check_completed.disconnect(handle_health)


def test_uninstall_signal(app_instance):
    uninstalled_events = []

    def handle_uninstall(sender, **kwargs):
        uninstalled_events.append(kwargs)

    subapp_uninstalled.connect(handle_uninstall)

    with app_instance.app_context():
        app_record = InstalledApp(
            name="App To Remove",
            slug="remove-app",
            source_type="git",
            entry_point="app:app",
            is_active=True,
        )
        db.session.add(app_record)
        db.session.commit()

        uninstall_app(app_record.id)

    assert len(uninstalled_events) >= 1
    assert uninstalled_events[-1]["app_slug"] == "remove-app"

    subapp_uninstalled.disconnect(handle_uninstall)
