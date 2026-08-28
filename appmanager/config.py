import os
from datetime import timedelta

from dotenv import load_dotenv

# Automatically load .env if present in current working directory or base directory
load_dotenv()

# Resolve base directory: when running as installed package, use current working directory
_pkg_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
if (
    "site-packages" in _pkg_root
    or "dist-packages" in _pkg_root
    or not os.path.exists(os.path.join(_pkg_root, "pyproject.toml"))
):
    DEFAULT_BASE_DIR = os.path.abspath(os.getcwd())
else:
    DEFAULT_BASE_DIR = _pkg_root

BASE_DIR = os.getenv("APPMANAGER_BASE_DIR", DEFAULT_BASE_DIR)


class Config:
    BASE_DIR = BASE_DIR
    TEMPLATES_AUTO_RELOAD = os.getenv("TEMPLATES_AUTO_RELOAD", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    SECRET_KEY = os.getenv(
        "SECRET_KEY", "appmanager-super-secret-key-change-in-production-min-32-chars"
    )
    JWT_SECRET = os.getenv("JWT_SECRET", "jwt-secret-key-change-in-production-min-32-chars")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_DAYS", 7)))
    MAGIC_LINK_EXPIRES_MINUTES = int(os.getenv("MAGIC_LINK_EXPIRES_MINUTES", 15))

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'appmanager.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Storage paths for sub-apps
    INSTALLED_APPS_DIR = os.getenv("INSTALLED_APPS_DIR", os.path.join(BASE_DIR, "installed_apps"))
    TEMP_UPLOAD_DIR = os.getenv("TEMP_UPLOAD_DIR", os.path.join(BASE_DIR, "instance", "uploads"))

    # OAuth Settings
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_DISCOVERY_URL = os.getenv(
        "GOOGLE_DISCOVERY_URL", "https://accounts.google.com/.well-known/openid-configuration"
    )

    # SMTP Settings (Optional, logs to console if empty)
    SMTP_SERVER = os.getenv("SMTP_SERVER", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "noreply@appmanager.local")
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")

    # Developer & Admin Provisioning Settings
    ALLOW_DEV_MAGIC_LOGIN = os.getenv("ALLOW_DEV_MAGIC_LOGIN", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    FIRST_USER_IS_ADMIN = os.getenv("FIRST_USER_IS_ADMIN", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    ADMIN_EMAILS = [
        e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
    ]

    # Virtual environment & sub-app execution mode ('singular' [default] or 'isolated')
    APP_VENV_MODE = os.getenv("APP_VENV_MODE", "singular").strip().lower()
    ALLOW_ISOLATED_APP_VENVS = os.getenv("ALLOW_ISOLATED_APP_VENVS", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    # Security settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT = os.getenv(
        "LOG_FORMAT",
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    )
