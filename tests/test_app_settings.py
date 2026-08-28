import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from appmanager import create_app, create_dispatchable_app
from appmanager.database import db
from appmanager.models import InstalledApp


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
            "SECRET_KEY": "test-secret",
            "JWT_SECRET": "test-jwt-secret",
        }
    )
    with app.app_context():
        db.create_all()
        app1 = InstalledApp(
            name="Sample App 1",
            slug="sample-counter",
            source_type="zip",
            entry_point="app:app",
            requires_auth=True,
            is_default=False,
            is_active=True,
        )
        app2 = InstalledApp(
            name="Template App 2",
            slug="template-app",
            source_type="zip",
            entry_point="app:app",
            requires_auth=False,
            is_default=False,
            is_active=True,
        )
        db.session.add_all([app1, app2])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_public_app_access_without_login(app):
    dispatchable = create_dispatchable_app(app)
    client = Client(dispatchable, Response)

    # Accessing template-app (requires_auth=False) should succeed directly without auth
    res = client.get("/apps/template-app/")
    assert res.status_code == 200
    assert b"Template Reference App" in res.data


def test_protected_app_access_requires_login(app):
    dispatchable = create_dispatchable_app(app)
    client = Client(dispatchable, Response)

    # Accessing sample-counter (requires_auth=True) without token returns 401 status page
    res = client.get("/apps/sample-counter/")
    assert res.status_code == 401
    assert b"Authentication Required" in res.data


def test_default_app_redirection(app):
    client = app.test_client()
    with app.app_context():
        target = InstalledApp.query.filter_by(slug="template-app").first()
        target.is_default = True
        db.session.commit()

    # Visiting / should redirect directly to /apps/template-app/
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert res.location == "/apps/template-app/"


def test_dashboard_route_still_accessible(app):
    client = app.test_client()
    res = client.get("/dashboard", follow_redirects=False)
    assert res.status_code == 302
    assert "/auth/login" in res.location
