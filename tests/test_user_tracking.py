from datetime import datetime, timedelta, timezone

import pytest

from appmanager import create_app
from appmanager.database import db
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
        admin = User(email="admin@test.com", name="Admin", role="admin", is_active=True)
        user = User(email="user@test.com", name="User", role="user", is_active=True)
        app1 = InstalledApp(name="App1", slug="app1", source_type="zip", is_active=True)
        db.session.add_all([admin, user, app1])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_user_online_status(app):
    with app.app_context():
        u = User.query.filter_by(email="user@test.com").first()
        assert not u.is_online()

        # Update last_active_at to now
        u.last_active_at = datetime.now(timezone.utc)
        db.session.commit()
        assert u.is_online()

        # Set last_active_at to 10 minutes ago
        u.last_active_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.session.commit()
        assert not u.is_online()


def test_admin_edit_user(app):
    client = app.test_client()
    with app.app_context():
        admin = User.query.filter_by(email="admin@test.com").first()
        target_user = User.query.filter_by(email="user@test.com").first()
        target_id = target_user.id

        # Authenticate admin session
        from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt

        token = generate_jwt(admin)
        client.set_cookie(JWT_COOKIE_NAME, token)

        res = client.post(
            f"/admin/users/{target_id}/edit",
            data={"name": "Updated User Name", "role": "admin", "is_active": "1", "perm_1": "1"},
            follow_redirects=True,
        )

        assert res.status_code == 200
        updated = db.session.get(User, target_id)
        assert updated.name == "Updated User Name"
        assert updated.role == "admin"
