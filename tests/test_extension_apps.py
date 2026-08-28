import pytest

from appmanager import create_app, create_dispatchable_app
from appmanager.database import db
from appmanager.extensions import (
    get_active_extensions,
    get_user_flair,
    render_user_flair_badge,
    set_extension_data,
)
from appmanager.models import InstalledApp, User


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
            "SECRET_KEY": "test-secret-key-that-is-at-least-32-chars-long",
            "JWT_SECRET": "test-jwt-secret-key-that-is-at-least-32-chars-long",
        }
    )
    with app.app_context():
        db.create_all()
        user = User(email="flair_test@example.com", name="Flair User", role="user")
        ext = InstalledApp(
            name="User Flairs Extension",
            slug="extension-flairs",
            source_type="zip",
            entry_point="extension:extension",
            app_type="extension",
            target_app="appmanager",
            requires_auth=False,
            is_active=True,
        )
        db.session.add_all([user, ext])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_extension_app_registration(app):
    with app.app_context():
        extensions = get_active_extensions("appmanager")
        assert len(extensions) == 1
        assert extensions[0].slug == "extension-flairs"
        assert extensions[0].app_type == "extension"


def test_extension_data_storage_and_flair_rendering(app):
    with app.app_context():
        user = User.query.filter_by(email="flair_test@example.com").first()

        # Set flair data
        set_extension_data(
            "extension-flairs",
            "user",
            user.id,
            {"title": "🚀 Lead Contributor", "color": "#38bdf8"},
        )

        # Retrieve flair data
        flair = get_user_flair(user.id)
        assert flair is not None
        assert flair["title"] == "🚀 Lead Contributor"
        assert flair["color"] == "#38bdf8"

        # Render Jinja badge HTML
        badge_html = str(render_user_flair_badge(user.id))
        assert "🚀 Lead Contributor" in badge_html
        assert "#38bdf8" in badge_html


def test_extension_app_wsgi_endpoint(app):
    from werkzeug.test import Client
    from werkzeug.wrappers import Response

    dispatchable = create_dispatchable_app(app)
    client = Client(dispatchable, Response)

    # Health check for extension app
    res = client.get("/apps/extension-flairs/health")
    assert res.status_code == 200
    assert b"extension-flairs" in res.data


def test_admin_manage_flairs_route(app):
    client = app.test_client()
    with app.app_context():
        admin = User(email="admin_flair@test.com", role="admin")
        user = User.query.filter_by(email="flair_test@example.com").first()
        db.session.add(admin)
        db.session.commit()

        from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt

        client.set_cookie(JWT_COOKIE_NAME, generate_jwt(admin))

        # Test POST /admin/flairs to assign flair
        res = client.post(
            "/admin/flairs",
            data={
                "user_id": str(user.id),
                "flair_title": "⭐ VIP Member",
                "flair_color": "#facc15",
                "action": "save",
            },
            follow_redirects=True,
        )
        assert res.status_code == 200

        flair = get_user_flair(user.id)
        assert flair is not None
        assert flair["title"] == "⭐ VIP Member"

        # Test GET /admin/flairs
        res_get = client.get("/admin/flairs")
        assert res_get.status_code == 200
        assert b"VIP Member" in res_get.data
