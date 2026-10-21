"""Tests for JWT token revocation via the per-user token_version."""

import pytest

from appmanager import create_app
from appmanager.auth.utils import (
    bump_token_version,
    decode_jwt,
    generate_jwt,
    get_current_user,
)
from appmanager.database import db
from appmanager.models import User


@pytest.fixture
def auth_app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-long-enough-for-hmac-123456",
            "JWT_SECRET": "test-jwt-secret-long-enough-for-hmac-1234",
        }
    )
    with app.app_context():
        user = User(email="user@example.com", name="User", role="user")
        db.session.add(user)
        db.session.commit()
    return app


def test_generated_token_carries_version(auth_app):
    with auth_app.app_context():
        user = User.query.first()
        token = generate_jwt(user)
        payload = decode_jwt(token)
    assert payload["tv"] == 0


def test_bump_invalidates_candidate_token(auth_app):
    with auth_app.test_request_context(headers={}):
        with auth_app.app_context():
            user = User.query.first()
            token = generate_jwt(user)
            bump_token_version(user)
            # Old token now carries a stale tv and must not authenticate.
            from appmanager.auth.utils import _token_version_valid

            payload = decode_jwt(token)
            assert not _token_version_valid(payload, user)


def test_logout_invalidates_cookie(auth_app):
    with auth_app.app_context():
        user = User.query.first()
        token = generate_jwt(user)

    client = auth_app.test_client()
    client.set_cookie("appmanager_jwt", token)
    resp = client.get("/auth/logout")
    assert resp.status_code in (301, 302)

    # Replaying the old cookie no longer resolves to a user.
    with auth_app.test_request_context(
        "/auth/profile", headers={"Cookie": f"appmanager_jwt={token}"}
    ):
        with auth_app.app_context():
            assert get_current_user() is None


def test_new_token_valid_after_bump(auth_app):
    with auth_app.test_request_context(headers={}):
        with auth_app.app_context():
            user = User.query.first()
            bump_token_version(user)
            fresh = generate_jwt(user)
            from appmanager.auth.utils import _token_version_valid

            assert _token_version_valid(decode_jwt(fresh), user)
