import pytest

from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME
from appmanager.database import db
from appmanager.models import MagicLinkToken, User


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-32-bytes-long-key-for-testing!",
            "JWT_SECRET": "test-jwt-secret-32-bytes-long-key-for-testing!",
            "INSTALLED_APPS_DIR": "/tmp/test_installed_apps",
            "TEMP_UPLOAD_DIR": "/tmp/test_uploads",
            "BASE_DIR": "/tmp",
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_magic_link_flow(client, app):
    # 1. Request magic link
    res = client.post("/auth/magic-link", data={"email": "test@example.com"})
    assert res.status_code == 200

    # 2. Check token in DB
    with app.app_context():
        token_record = MagicLinkToken.query.filter_by(email="test@example.com").first()
        assert token_record is not None
        token_str = token_record.token

    # 3. Verify magic link
    res_verify = client.get(f"/auth/verify-magic?token={token_str}", follow_redirects=True)
    assert res_verify.status_code == 200
    assert client.get_cookie(JWT_COOKIE_NAME) is not None

    # 4. Check user created in DB as first user (Admin)
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        assert user is not None
        assert user.role == "admin"


def test_second_user_is_regular_user(client, app):
    # Create first user
    with app.app_context():
        u1 = User(email="admin@example.com", role="admin")
        db.session.add(u1)
        db.session.commit()

    # Request & verify magic link for second user
    client.post("/auth/magic-link", data={"email": "user2@example.com"})
    with app.app_context():
        t = MagicLinkToken.query.filter_by(email="user2@example.com").first()
        token_str = t.token

    client.get(f"/auth/verify-magic?token={token_str}")
    with app.app_context():
        u2 = User.query.filter_by(email="user2@example.com").first()
        assert u2 is not None
        assert u2.role == "user"
