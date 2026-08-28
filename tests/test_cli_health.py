import pytest

from appmanager import create_app
from appmanager.cli import run_scheduled_tasks
from appmanager.database import db
from appmanager.health import check_all_apps_health
from appmanager.models import AppHealthLog, AppTelemetryLog, InstalledApp


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
        }
    )
    with app.app_context():
        db.create_all()
        # Seed test template app
        sample = InstalledApp(
            name="Template Reference App",
            slug="template-app",
            source_type="zip",
            entry_point="app:app",
            is_active=True,
        )
        db.session.add(sample)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_health_check_execution(app):
    with app.app_context():
        results = check_all_apps_health()
        assert len(results) == 1
        slug, status, time_ms = results[0]
        assert slug == "template-app"
        assert status == "healthy"
        assert time_ms > 0


def test_cli_scheduled_tasks(app):
    with app.app_context():
        run_scheduled_tasks(app)
        logs = AppHealthLog.query.filter_by(app_id=1).all()
        assert len(logs) >= 1
        telemetry = AppTelemetryLog.query.filter_by(app_slug="template-app").all()
        assert len(telemetry) >= 1
