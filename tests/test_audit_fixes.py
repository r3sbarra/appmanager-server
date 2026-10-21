"""
Regression tests for the three audit fixes:

1. Host-wide CSRF enforcement (``AppManager._register_csrf_guard``).
2. No permanent ``sys.path`` pollution from sub-app imports, and a real module
   purge on cache clear.
3. Scoped SQLite access is actually isolated (per-app database file).
"""

import os

import pytest

from appmanager import create_app
from appmanager.admin.app_installer import (
    load_wsgi_app_from_path,
    purge_installed_app_modules,
)
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.database import db
from appmanager.db_access import get_db_engine, grant_permission
from appmanager.models import InstalledApp, User
from tests.csrf_helpers import post_with_csrf


def _make_app(tmp_path):
    return create_app(
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


def _make_admin(app, email="admin@test.local"):
    with app.app_context():
        admin = User(email=email, role="admin", is_active=True)
        db.session.add(admin)
        db.session.commit()
        return admin.id


# ---------------------------------------------------------------------------
# 1. CSRF enforcement
# ---------------------------------------------------------------------------


def test_admin_post_without_token_is_rejected(tmp_path):
    """An authenticated admin POST with no token must not reach the view."""
    app = _make_app(tmp_path)
    admin_id = _make_admin(app)

    with app.app_context():
        app_rec = InstalledApp(name="X", slug="x", source_type="zip", is_active=True)
        db.session.add(app_rec)
        db.session.commit()
        rec_id = app_rec.id
        token = generate_jwt(db.session.get(User, admin_id))

    client = app.test_client()
    client.set_cookie(JWT_COOKIE_NAME, token)
    res = client.post(f"/admin/apps/{rec_id}/toggle-auth")
    assert res.status_code == 400
    assert b"CSRF validation failed" in res.data

    # The mutation did not happen.
    with app.app_context():
        assert db.session.get(InstalledApp, rec_id).requires_auth is True


def test_admin_post_with_token_succeeds(tmp_path):
    app = _make_app(tmp_path)
    admin_id = _make_admin(app)

    with app.app_context():
        app_rec = InstalledApp(name="X", slug="x", source_type="zip", is_active=True)
        db.session.add(app_rec)
        db.session.commit()
        rec_id = app_rec.id
        token = generate_jwt(db.session.get(User, admin_id))

    client = app.test_client()
    client.set_cookie(JWT_COOKIE_NAME, token)
    res = post_with_csrf(client, f"/admin/apps/{rec_id}/toggle-auth")
    assert res.status_code == 302

    with app.app_context():
        assert db.session.get(InstalledApp, rec_id).requires_auth is False


def test_forged_token_rejected(tmp_path):
    app = _make_app(tmp_path)
    admin_id = _make_admin(app)
    with app.app_context():
        token = generate_jwt(db.session.get(User, admin_id))

    client = app.test_client()
    client.set_cookie(JWT_COOKIE_NAME, token)
    res = client.post("/admin/roles/create", data={"name": "Evil", "csrf_token": "1:deadbeef"})
    assert res.status_code == 400


def test_api_routes_exempt_from_csrf(tmp_path):
    """The REST API authenticates by key/Bearer, not cookies, so no token needed."""
    app = _make_app(tmp_path)
    app.config["APPMANAGER_API_KEY"] = "test-api-key"
    client = app.test_client()
    res = client.post(
        "/api/v1/roles",
        json={"name": "API Role", "slug": "api-role"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert res.status_code == 201


def test_csrf_guard_can_be_disabled_explicitly(tmp_path):
    app = _make_app(tmp_path)
    app.config["CSRF_PROTECT"] = False
    admin_id = _make_admin(app)
    with app.app_context():
        token = generate_jwt(db.session.get(User, admin_id))
    client = app.test_client()
    client.set_cookie(JWT_COOKIE_NAME, token)
    # No token, but the guard is off: the request reaches the view (302 redirect).
    assert client.post("/admin/roles/create", data={"name": "N"}).status_code == 302


# ---------------------------------------------------------------------------
# 2. sys.path hygiene + module purge
# ---------------------------------------------------------------------------


def _write_app(app_dir, body):
    os.makedirs(app_dir, exist_ok=True)
    with open(os.path.join(app_dir, "app.py"), "w") as f:
        f.write(body)


def test_loading_subapp_does_not_pollute_sys_path(tmp_path):
    import sys

    app_dir = str(tmp_path / "apps" / "demo")
    _write_app(app_dir, "from flask import Flask\napp = Flask(__name__)\n")
    before = list(sys.path)

    load_wsgi_app_from_path(app_dir, "app:app")

    assert sys.path == before, "sub-app import left a permanent sys.path entry"


def test_two_apps_with_same_module_name_do_not_shadow(tmp_path):
    """Both apps ship a top-level ``helper.py``; each must see its own."""
    import sys

    for slug, value in (("app-one", "ONE"), ("app-two", "TWO")):
        app_dir = str(tmp_path / "apps" / slug)
        _write_app(
            app_dir,
            "from flask import Flask\n"
            "import helper\n"
            "app = Flask(__name__)\n"
            "app.config['VALUE'] = helper.VALUE\n",
        )
        with open(os.path.join(app_dir, "helper.py"), "w") as f:
            f.write(f"VALUE = {value!r}\n")

    first = load_wsgi_app_from_path(str(tmp_path / "apps" / "app-one"), "app:app")
    second = load_wsgi_app_from_path(str(tmp_path / "apps" / "app-two"), "app:app")
    assert first.config["VALUE"] == "ONE"
    assert second.config["VALUE"] == "TWO"
    # And neither left the app dirs on sys.path.
    assert str(tmp_path / "apps" / "app-one") not in sys.path


def test_submodules_do_not_leak_between_apps(tmp_path):
    """A submodule imported by app A must not be served to app B."""
    import sys

    for slug, value in (("leak-one", 1), ("leak-two", 2)):
        app_dir = str(tmp_path / "apps" / slug)
        _write_app(
            app_dir,
            "from flask import Flask\nimport submod\napp = Flask(__name__)\n"
            "app.config['VALUE'] = submod.VALUE\n",
        )
        with open(os.path.join(app_dir, "submod.py"), "w") as f:
            f.write(f"VALUE = {value}\n")

    first = load_wsgi_app_from_path(str(tmp_path / "apps" / "leak-one"), "app:app")
    second = load_wsgi_app_from_path(str(tmp_path / "apps" / "leak-two"), "app:app")
    assert first.config["VALUE"] == 1
    assert second.config["VALUE"] == 2
    # The bare name is not left cached globally for either app.
    assert "submod" not in sys.modules


def test_purge_evicts_scoped_modules(tmp_path):
    import sys

    app_dir = str(tmp_path / "apps" / "reloadme")
    _write_app(app_dir, "from flask import Flask\napp = Flask(__name__)\n")
    load_wsgi_app_from_path(app_dir, "app:app")

    scoped = [n for n in sys.modules if n.startswith("appmanager.installed.reloadme")]
    assert scoped, "entry module was not registered under its scoped name"

    evicted = purge_installed_app_modules("reloadme")
    assert evicted == len(scoped)
    assert not [n for n in sys.modules if n.startswith("appmanager.installed.reloadme")]


def test_clear_cache_purges_modules(tmp_path):
    app = _make_app(tmp_path)
    app_dir = str(tmp_path / "installed_apps" / "cached")
    _write_app(app_dir, "from flask import Flask\napp = Flask(__name__)\n")

    dispatcher = app.extensions["appmanager"].create_dispatcher(app)
    dispatcher.sub_app_cache["cached"] = lambda e, s: []
    dispatcher.clear_cache("cached")
    assert "cached" not in dispatcher.sub_app_cache


# ---------------------------------------------------------------------------
# 3. Scoped SQLite isolation
# ---------------------------------------------------------------------------


def test_scoped_sqlite_engine_uses_separate_file(tmp_path):
    """Scoped access must not hand the app the host database."""
    app = _make_app(tmp_path)
    with app.app_context():
        host_id = _make_admin(app)
        app_rec = InstalledApp(name="Scoped", slug="scoped", source_type="zip", is_active=True)
        db.session.add(app_rec)
        db.session.commit()
        app_id = app_rec.id
        grant_permission(app_id, "db", "scoped", host_id)

        engine = get_db_engine("scoped")
        assert engine is not None

        # The app's engine points at its own file, not the host DB.
        url = str(engine.url)
        assert "scoped.db" in url
        assert url != str(db.engine.url)

        # And it genuinely cannot see host tables.
        from sqlalchemy import inspect, text

        assert "users" not in inspect(engine).get_table_names()

        with engine.connect() as conn:
            with pytest.raises(Exception):
                conn.execute(text("SELECT * FROM users")).fetchall()


def test_full_access_still_reaches_host_db(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        host_id = _make_admin(app)
        app_rec = InstalledApp(name="Full", slug="fullapp", source_type="zip", is_active=True)
        db.session.add(app_rec)
        db.session.commit()
        app_id = app_rec.id
        grant_permission(app_id, "db", "full", host_id)

        engine = get_db_engine("fullapp")
        from sqlalchemy import inspect

        assert "users" in inspect(engine).get_table_names()
