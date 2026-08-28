import io
import os
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from appmanager import create_app, create_dispatchable_app
from appmanager.admin.app_installer import (
    install_from_zip,
)
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.bridge import get_current_user_from_headers
from appmanager.database import db
from appmanager.models import InstalledApp, User, UserAppPermission
from appmanager.security import (
    MAX_ZIP_FILE_COUNT,
    check_rate_limit,
    generate_csrf_token,
    is_safe_redirect_url,
    is_safe_repo_url,
    validate_csrf_token,
    validate_entrypoint_path,
)


def test_bridge_get_current_user_from_headers():
    headers = {
        "X-AppManager-User-Id": "42",
        "X-AppManager-User-Email": "dev@example.com",
        "X-AppManager-User-Role": "admin",
    }
    user_info = get_current_user_from_headers(headers)
    assert user_info is not None
    assert user_info["id"] == 42
    assert user_info["email"] == "dev@example.com"
    assert user_info["role"] == "admin"


def test_wsgi_user_header_forwarding(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    # Sub-app that reflects received headers
    app_dir = tmp_path / "installed_apps" / "header-echo-app"
    os.makedirs(app_dir, exist_ok=True)
    with open(app_dir / "app.py", "w") as f:
        f.write("""from flask import Flask, jsonify, request
from appmanager.bridge import get_current_user_from_headers
app = Flask(__name__)
@app.route('/')
def index():
    user = get_current_user_from_headers(request.headers)
    return jsonify({
        'user': user,
        'forwarded_prefix': request.headers.get('X-Forwarded-Prefix')
    })
""")

    with app.app_context():
        user = User(email="testuser@example.com", name="Test User", role="admin")
        db.session.add(user)
        db.session.commit()
        jwt_token = generate_jwt(user)

        rec = InstalledApp(
            name="Header Echo",
            slug="header-echo-app",
            source_type="git",
            entry_point="app:app",
            requires_auth=True,
            is_active=True,
        )
        db.session.add(rec)
        db.session.commit()

        perm = UserAppPermission(user_id=user.id, app_id=rec.id, can_access=True)
        db.session.add(perm)
        db.session.commit()

    dispatcher = create_dispatchable_app(app)
    app.wsgi_app = dispatcher
    client = app.test_client()

    # Request without auth
    res = client.get("/apps/header-echo-app/")
    assert res.status_code == 401

    # Request with JWT cookie
    client.set_cookie(JWT_COOKIE_NAME, jwt_token)
    res = client.get("/apps/header-echo-app/")
    assert res.status_code == 200
    data = res.get_json()
    assert data["user"] is not None
    assert data["user"]["email"] == "testuser@example.com"
    assert data["user"]["role"] == "admin"
    assert data["forwarded_prefix"] == "/apps/header-echo-app"


def test_header_spoofing_defense(tmp_path):
    """
    Ensure untrusted client cannot spoof HTTP_X_APPMANAGER_* headers on public sub-apps.
    """
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    app_dir = tmp_path / "installed_apps" / "public-echo-app"
    os.makedirs(app_dir, exist_ok=True)
    with open(app_dir / "app.py", "w") as f:
        f.write("""from flask import Flask, jsonify, request
from appmanager.bridge import get_current_user_from_headers
app = Flask(__name__)
@app.route('/')
def index():
    user = get_current_user_from_headers(request.headers)
    return jsonify({'user': user})
""")

    with app.app_context():
        rec = InstalledApp(
            name="Public Echo",
            slug="public-echo-app",
            source_type="git",
            entry_point="app:app",
            requires_auth=False,
            is_active=True,
        )
        db.session.add(rec)
        db.session.commit()

    dispatcher = create_dispatchable_app(app)
    app.wsgi_app = dispatcher
    client = app.test_client()

    # Attempt to spoof headers as an attacker
    spoofed_headers = {
        "X-AppManager-User-Id": "1",
        "X-AppManager-User-Email": "victim_admin@example.com",
        "X-AppManager-User-Role": "admin",
    }
    res = client.get("/apps/public-echo-app/", headers=spoofed_headers)
    assert res.status_code == 200
    data = res.get_json()
    # User must be None because the attacker has no valid JWT
    assert data["user"] is None


def test_zip_bomb_protection(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(MAX_ZIP_FILE_COUNT + 10):
            zf.writestr(f"file_{i}.txt", "dummy content")
    zip_bytes.seek(0)

    storage = FileStorage(stream=zip_bytes, filename="bomb.zip", content_type="application/zip")

    with app.app_context():
        with pytest.raises(Exception) as exc_info:
            install_from_zip(storage, name="Bomb App", slug="bomb-app")
        assert "Security error" in str(exc_info.value)


def test_csrf_token_validation(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "my-super-secret-key-at-least-32-bytes-long!"})

    with app.test_request_context():
        token = generate_csrf_token()
        assert token is not None
        assert validate_csrf_token(token) is True
        assert validate_csrf_token("invalid:token") is False
        assert validate_csrf_token("") is False
        assert validate_csrf_token(None) is False


def test_open_redirect_defense(tmp_path):
    app = create_app({"TESTING": True})

    with app.test_request_context(base_url="http://localhost:5000"):
        assert is_safe_redirect_url("/dashboard") is True
        assert is_safe_redirect_url("/apps/my-app/") is True
        assert is_safe_redirect_url("https://evil.com/phishing") is False
        assert is_safe_redirect_url("//evil.com") is False
        assert is_safe_redirect_url("javascript:alert(1)") is False
        assert is_safe_redirect_url(None) is False


def test_git_repo_url_security():
    assert is_safe_repo_url("https://github.com/org/repo.git") is True
    assert is_safe_repo_url("git@github.com:org/repo.git") is True
    assert is_safe_repo_url("ssh://git@github.com/org/repo.git") is True
    assert is_safe_repo_url("--upload-pack=evil") is False
    assert is_safe_repo_url("-oProxyCommand=calc.exe") is False
    assert is_safe_repo_url("file:///etc/passwd") is False


def test_entrypoint_path_traversal_defense(tmp_path):
    app_dir = str(tmp_path / "app_dir")
    os.makedirs(app_dir, exist_ok=True)

    is_safe, _ = validate_entrypoint_path(app_dir, "app:app")
    assert is_safe is True

    is_safe, err = validate_entrypoint_path(app_dir, "../../evil:app")
    assert is_safe is False
    assert "cannot contain path separators" in err or "escapes" in err


def test_auth_rate_limiter():
    key = "test_rate_limiter_ip"
    # Allow 3 attempts within window
    for _ in range(3):
        assert check_rate_limit(key, limit=3, window_seconds=10) is True

    # 4th attempt should be blocked
    assert check_rate_limit(key, limit=3, window_seconds=10) is False


def test_sensitive_data_redaction():
    import logging

    from appmanager.security import SensitiveDataFilter, redact_sensitive_data

    raw_log = "User failed auth with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test and token=secret12345"
    sanitized = redact_sensitive_data(raw_log)
    assert "eyJhbGci" not in sanitized
    assert "[REDACTED]" in sanitized

    # Test SensitiveDataFilter
    filt = SensitiveDataFilter()
    rec = logging.LogRecord(
        "appmanager", logging.INFO, "path", 10, "Accessing token=super_secret_tok", (), None
    )
    filt.filter(rec)
    assert "super_secret_tok" not in rec.msg
    assert "[REDACTED]" in rec.msg
