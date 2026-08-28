import pytest

from appmanager import create_app, create_dispatchable_app
from appmanager.database import db


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
        yield app
        db.session.remove()
        db.drop_all()


def test_404_error_page(app):
    client = app.test_client()
    res = client.get("/non-existent-route-path-1234")
    assert res.status_code == 404
    assert b"404" in res.data
    assert b"Page or App Not Found" in res.data
    assert b"Return to Home" in res.data
    assert b"Admin Control Panel" not in res.data


def test_uninstalled_subapp_dispatcher_404(app):
    dispatchable = create_dispatchable_app(app)
    from werkzeug.test import Client
    from werkzeug.wrappers import Response

    client = Client(dispatchable, Response)
    res = client.get("/apps/uninstalled-slug-xyz/")
    assert res.status_code == 404
    assert b"App Not Found" in res.data
    assert b"Return to Home" in res.data
    assert b"Admin Control Panel" not in res.data
