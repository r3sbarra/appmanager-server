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
            "ALLOW_DEV_MAGIC_LOGIN": True,  # Default to True in base test fixture
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


def test_magic_link_fails_without_terms_acceptance(client):
    res = client.post("/auth/magic-link", data={"email": "test@example.com"}, follow_redirects=True)
    assert res.status_code == 200
    assert b"You must agree to the Terms of Use and Privacy Policy" in res.data


def test_magic_link_flow_dev_mode(client, app):
    # 1. Request magic link with ALLOW_DEV_MAGIC_LOGIN=True
    res = client.post("/auth/magic-link", data={"email": "test@example.com", "accept_terms": "true"})
    assert res.status_code == 200
    assert b"Developer Mode Preview" in res.data

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


def test_magic_link_fails_when_smtp_and_dev_mode_disabled(app):
    app.config["ALLOW_DEV_MAGIC_LOGIN"] = False
    app.config["SMTP_SERVER"] = ""
    client = app.test_client()

    res = client.post("/auth/magic-link", data={"email": "user@example.com", "accept_terms": "true"}, follow_redirects=True)
    assert res.status_code == 200
    assert b"Email delivery (SMTP) is not configured" in res.data

    with app.app_context():
        token = MagicLinkToken.query.filter_by(email="user@example.com").first()
        assert token is None


def test_admin_emails_config_provisioning(client, app):
    app.config["ALLOW_DEV_MAGIC_LOGIN"] = True
    app.config["ADMIN_EMAILS"] = ["designated-admin@example.com"]
    app.config["FIRST_USER_IS_ADMIN"] = False

    # First user who is NOT in ADMIN_EMAILS
    client.post("/auth/magic-link", data={"email": "regular@example.com", "accept_terms": "true"})
    with app.app_context():
        t1 = MagicLinkToken.query.filter_by(email="regular@example.com").first()
        token1 = t1.token

    client.get(f"/auth/verify-magic?token={token1}")
    with app.app_context():
        u1 = User.query.filter_by(email="regular@example.com").first()
        assert u1 is not None
        assert u1.role == "user"

    # Second user who IS in ADMIN_EMAILS
    client.post("/auth/magic-link", data={"email": "designated-admin@example.com", "accept_terms": "true"})
    with app.app_context():
        t2 = MagicLinkToken.query.filter_by(email="designated-admin@example.com").first()
        token2 = t2.token

    client.get(f"/auth/verify-magic?token={token2}")
    with app.app_context():
        u2 = User.query.filter_by(email="designated-admin@example.com").first()
        assert u2 is not None
        assert u2.role == "admin"


def test_second_user_is_regular_user(client, app):
    app.config["ALLOW_DEV_MAGIC_LOGIN"] = True
    # Create first user
    with app.app_context():
        u1 = User(email="admin@example.com", role="admin")
        db.session.add(u1)
        db.session.commit()

    # Request & verify magic link for second user
    client.post("/auth/magic-link", data={"email": "user2@example.com", "accept_terms": "true"}, environ_base={"REMOTE_ADDR": "127.0.0.99"})
    with app.app_context():
        t = MagicLinkToken.query.filter_by(email="user2@example.com").first()
        token_str = t.token

    client.get(f"/auth/verify-magic?token={token_str}")
    with app.app_context():
        u2 = User.query.filter_by(email="user2@example.com").first()
        assert u2 is not None
        assert u2.role == "user"
