"""
Regression tests for the forwarded-identity header contract.

The SDK (``appmanager_sdk.client``) documents and enforces an optional
``X-AppManager-Signature`` (HMAC-SHA256 over ``"id|email|role"``). Historically
the host never emitted it, so any operator who followed the docs and configured a
header secret on a sub-app silently lost authentication on every request.

These tests pin both halves of the contract:
  * the dispatcher emits a signature the SDK accepts,
  * a tampered/absent signature is rejected,
  * ``X-AppManager-User-Name`` is populated so ``display_name`` is not empty.
"""

import hashlib
import hmac

import pytest
from appmanager_sdk.client import AppManagerClient

from appmanager import create_app, create_dispatchable_app
from appmanager.database import db
from appmanager.models import InstalledApp, User

HEADER_SECRET = "header-secret-for-tests-min-32-chars"


def _echo_app_source(module_body: str) -> str:
    """Sub-app that returns the forwarded AppManager headers it received.

    Reads ``request.environ`` (not ``request.headers``) because the dispatcher
    injects the WSGI ``HTTP_X_APPMANAGER_*`` form directly into the environ.
    """
    return (
        "from flask import Flask, jsonify, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/')\n"
        "def home():\n"
        "    return jsonify({k: v for k, v in request.environ.items() if 'APPMANAGER' in k})\n"
        + module_body
    )


@pytest.fixture
def host(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'host.db'}",
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
            "APPMANAGER_HEADER_SECRET": HEADER_SECRET,
        }
    )
    return app


def _install_echo_app(app, slug="echo", requires_auth=True):
    from appmanager.admin.app_installer import load_wsgi_app_from_path  # noqa: F401

    app_dir = str(app.config["INSTALLED_APPS_DIR"] + "/" + slug)
    import os

    os.makedirs(app_dir, exist_ok=True)
    with open(os.path.join(app_dir, "app.py"), "w") as f:
        f.write(_echo_app_source(""))
    with app.app_context():
        rec = InstalledApp(
            name="Echo",
            slug=slug,
            source_type="zip",
            is_active=True,
            requires_auth=requires_auth,
            entry_point="app:app",
        )
        db.session.add(rec)
        db.session.commit()
        return rec.slug  # plain value: the ORM instance detaches when the context closes


def _expect(payload_headers: dict, secret: str) -> str:
    payload = "|".join(
        [
            payload_headers.get("X-AppManager-User-Id", ""),
            payload_headers.get("X-AppManager-User-Email", ""),
            payload_headers.get("X-AppManager-User-Role", ""),
        ]
    )
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _sdk_headers(forwarded: dict) -> dict:
    """
    Translate the sub-app's WSGI environ keys into the header names the SDK reads.

    The dispatcher injects ``HTTP_X_APPMANAGER_*``; ``AppManagerClient`` looks up
    the same values under ``X-AppManager-*`` (or the HTTP_-prefixed form).
    """
    return {
        "X-AppManager-User-Id": forwarded.get("HTTP_X_APPMANAGER_USER_ID", ""),
        "X-AppManager-User-Email": forwarded.get("HTTP_X_APPMANAGER_USER_EMAIL", ""),
        "X-AppManager-User-Role": forwarded.get("HTTP_X_APPMANAGER_USER_ROLE", ""),
        "X-AppManager-Signature": forwarded.get("HTTP_X_APPMANAGER_SIGNATURE", ""),
    }


def _make_user(app, email="user@test.local", name="Test User", role="admin"):
    with app.app_context():
        user = User(email=email, name=name, role=role, is_active=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _jwt_for(app, user_id):
    from appmanager.auth.utils import generate_jwt

    with app.app_context():
        return generate_jwt(db.session.get(User, user_id))


def test_dispatcher_emits_signature_the_sdk_accepts(host):
    """End-to-end: host signs -> sub-app's SDK client verifies."""
    user_id = _make_user(host)
    _install_echo_app(host)
    token = _jwt_for(host, user_id)

    wsgi = create_dispatchable_app(host)
    from werkzeug.test import Client

    client = Client(wsgi)
    res = client.get("/apps/echo/", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

    forwarded = res.get_json()
    headers = _sdk_headers(forwarded)
    assert headers["X-AppManager-Signature"], "dispatcher emitted no signature"

    sdk = AppManagerClient(app_slug="echo", header_secret=HEADER_SECRET)
    user = sdk.get_current_user(headers)
    assert user is not None, "SDK rejected a signature the host produced"
    assert user["email"] == "user@test.local"
    assert user["is_admin"] is True


def test_signature_matches_expected_canonical_payload(host):
    user_id = _make_user(host, email="alice@test.local", name="Alice")
    _install_echo_app(host)
    token = _jwt_for(host, user_id)

    from werkzeug.test import Client

    res = Client(create_dispatchable_app(host)).get(
        "/apps/echo/", headers={"Authorization": f"Bearer {token}"}
    )
    forwarded = res.get_json()
    headers = _sdk_headers(forwarded)
    expected = _expect(headers, HEADER_SECRET)
    assert headers["X-AppManager-Signature"] == expected


def test_sdk_rejects_wrong_secret(host):
    """A sub-app configured with a different secret must not authenticate."""
    user_id = _make_user(host)
    _install_echo_app(host)
    token = _jwt_for(host, user_id)

    from werkzeug.test import Client

    res = Client(create_dispatchable_app(host)).get(
        "/apps/echo/", headers={"Authorization": f"Bearer {token}"}
    )
    headers = _sdk_headers(res.get_json())
    sdk = AppManagerClient(app_slug="echo", header_secret="a-different-secret-entirely")
    assert sdk.get_current_user(headers) is None


def test_no_secret_configured_means_no_signature_header(tmp_path):
    """Default deployment keeps the gateway-only model: no signature emitted."""
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'host.db'}",
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )
    assert app.config.get("APPMANAGER_HEADER_SECRET", "") == ""
    user_id = _make_user(app)
    _install_echo_app(app)
    token = _jwt_for(app, user_id)

    from werkzeug.test import Client

    res = Client(create_dispatchable_app(app)).get(
        "/apps/echo/", headers={"Authorization": f"Bearer {token}"}
    )
    forwarded = res.get_json()
    assert not any("SIGNATURE" in k.upper() for k in forwarded)
    # Identity headers are still injected; only the signature is absent.
    assert forwarded.get("HTTP_X_APPMANAGER_USER_ID")


def test_user_name_header_is_populated(host):
    """get_auth_context() must not return an empty display_name."""
    user_id = _make_user(host, email="bob@test.local", name="Bob Builder")
    rec_slug = _install_echo_app(host)
    token = _jwt_for(host, user_id)

    from werkzeug.test import Client

    res = Client(create_dispatchable_app(host)).get(
        "/apps/echo/", headers={"Authorization": f"Bearer {token}"}
    )
    forwarded = res.get_json()
    assert forwarded.get("HTTP_X_APPMANAGER_USER_NAME") == "Bob Builder", forwarded
    assert rec_slug == "echo"


def test_auth_context_returns_display_name(host):
    """The db_access surface reads the header the dispatcher now sets."""
    from appmanager.db_access import ensure_permission_rows, get_auth_context

    user_id = _make_user(host, email="carol@test.local", name="Carol Danvers")
    _install_echo_app(host)
    with host.app_context():
        rec = InstalledApp.query.filter_by(slug="echo").first()
        ensure_permission_rows(rec, {"requests_auth_readonly": True})
        from appmanager.models import AppDbPermission

        perm = AppDbPermission.query.filter_by(
            app_id=rec.id, permission_type="auth_readonly"
        ).first()
        perm.granted = True
        db.session.commit()

        ctx = get_auth_context(
            "echo",
            {
                "HTTP_X_APPMANAGER_USER_ID": str(user_id),
                "HTTP_X_APPMANAGER_USER_NAME": "Carol Danvers",
                "HTTP_X_APPMANAGER_USER_ROLE": "user",
            },
        )
        assert ctx is not None
        assert ctx["display_name"] == "Carol Danvers"
        assert ctx["authenticated"] is True


def test_name_falls_back_to_email_when_unset(host):
    user_id = _make_user(host, email="noname@test.local", name=None)
    _install_echo_app(host)
    token = _jwt_for(host, user_id)

    from werkzeug.test import Client

    res = Client(create_dispatchable_app(host)).get(
        "/apps/echo/", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.get_json().get("HTTP_X_APPMANAGER_USER_NAME") == "noname@test.local"
