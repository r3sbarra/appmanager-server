"""Tests for the production secret guard and CSRF secret handling."""

import pytest

from appmanager import create_app
from appmanager.config import DEV_JWT_SECRET, DEV_SECRET_KEY


def _base_config(tmp_path, **overrides):
    cfg = {
        "BASE_DIR": str(tmp_path),
        "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
        "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "SECRET_KEY": "test-secret-key-which-is-long-enough-123456",
        "JWT_SECRET": "test-jwt-secret-key-which-is-long-enough-1234",
    }
    cfg.update(overrides)
    return cfg


def test_production_rejects_default_secrets(tmp_path):
    with pytest.raises(RuntimeError) as excinfo:
        create_app(
            _base_config(
                tmp_path,
                ENV="production",
                SECRET_KEY=DEV_SECRET_KEY,
                JWT_SECRET=DEV_JWT_SECRET,
            )
        )
    assert "SECRET_KEY" in str(excinfo.value)
    assert "JWT_SECRET" in str(excinfo.value)


def test_production_rejects_short_secret(tmp_path):
    with pytest.raises(RuntimeError) as excinfo:
        create_app(
            _base_config(
                tmp_path,
                ENV="production",
                SECRET_KEY="short",
                JWT_SECRET="also-short",
            )
        )
    assert "insecure secret" in str(excinfo.value).lower()


def test_production_accepts_strong_secrets(tmp_path):
    app = create_app(
        _base_config(
            tmp_path,
            ENV="production",
            SECRET_KEY="a-very-strong-secret-key-of-at-least-32-chars",
            JWT_SECRET="a-very-strong-jwt-secret-of-at-least-32-chars",
        )
    )
    assert app.config["ENV"] == "production"


def test_development_allows_default_secrets(tmp_path):
    # Non-production, non-testing apps may keep dev defaults for local use.
    app = create_app(_base_config(tmp_path, ENV="development"))
    assert app.config["SECRET_KEY"] == "test-secret-key-which-is-long-enough-123456"


def test_testing_is_exempt(tmp_path):
    app = create_app(
        _base_config(
            tmp_path,
            TESTING=True,
            ENV="production",
            SECRET_KEY=DEV_SECRET_KEY,
            JWT_SECRET=DEV_JWT_SECRET,
        )
    )
    assert app.config["TESTING"] is True


def test_missing_secret_raises_on_csrf(tmp_path):
    """A blank SECRET_KEY must not silently fall back to a literal."""
    from appmanager.security import get_security_secret

    app = create_app(_base_config(tmp_path, SECRET_KEY=""))
    app.config["SECRET_KEY"] = ""
    with app.app_context():
        with pytest.raises(RuntimeError):
            get_security_secret()
