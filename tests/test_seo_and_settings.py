"""
Tests for the SEO capabilities and host Settings page added 2026-08-28.

Covers:
- Host settings model + helpers (get/set, defaults).
- SEO injection into sub-app HTML via the dispatcher middleware.
- robots.txt / sitemap.xml routes.
- Admin Settings page (SEO, Dashboard/Login, Visibility sections) + save.
- Dashboard visibility button labels (Login / Permission Required / Launch).
"""

import os

from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.database import db
from appmanager.host_settings import (
    DEFAULT_HOST_SETTINGS,
    get_host_setting,
    get_host_settings,
    set_host_setting,
    set_host_settings,
)
from appmanager.middleware import DynamicAppDispatcherMiddleware
from appmanager.models import InstalledApp, User, UserAppPermission


def _make_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )


def _seed_admin(app):
    with app.app_context():
        admin = User(email="admin@example.com", name="Admin", role="admin", is_active=True)
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    return admin_id


# ---------- Host settings ----------


def test_host_settings_defaults_and_roundtrip(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        settings = get_host_settings()
        # All canonical defaults present.
        for key in DEFAULT_HOST_SETTINGS:
            assert key in settings
        assert settings["seo_enabled"] is True
        assert settings["dashboard_enabled"] is True
        assert settings["visibility_show_auth_apps"] is True

        # set + get roundtrip
        set_host_setting("seo_enabled", False)
        assert get_host_setting("seo_enabled") is False
        assert get_host_settings()["seo_enabled"] is False

        # bulk set
        set_host_settings({"dashboard_enabled": False, "seo_sitemap_enabled": False})
        assert get_host_setting("dashboard_enabled") is False
        assert get_host_setting("seo_sitemap_enabled") is False


# ---------- robots.txt / sitemap.xml ----------


def test_robots_txt(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        res = client.get("/robots.txt")
        assert res.status_code == 200
        body = res.get_data(as_text=True)
        assert "User-agent: *" in body
        assert "Allow: /" in body


def test_sitemap_xml_requires_canonical_base(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        # No canonical base -> 404
        assert client.get("/sitemap.xml").status_code == 404
        # With base + a public app -> lists it
        set_host_setting("seo_portal_canonical_base", "https://example.com")
        pub = InstalledApp(
            name="Public",
            slug="public",
            source_type="zip",
            entry_point="app:app",
            requires_auth=False,
            has_web_ui=True,
            is_active=True,
        )
        auth = InstalledApp(
            name="Auth",
            slug="auth",
            source_type="zip",
            entry_point="app:app",
            requires_auth=True,
            has_web_ui=True,
            is_active=True,
        )
        db.session.add_all([pub, auth])
        db.session.commit()
        res = client.get("/sitemap.xml")
        assert res.status_code == 200
        body = res.get_data(as_text=True)
        assert "https://example.com/apps/public/" in body
        assert "https://example.com/apps/auth/" not in body  # auth app excluded


# ---------- SEO injection into sub-app HTML ----------


def test_seo_injection_into_subapp(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        a = InstalledApp(
            name="SEO App",
            slug="seoapp",
            source_type="zip",
            entry_point="app:app",
            requires_auth=False,
            has_web_ui=True,
            is_active=True,
        )
        a.set_seo(
            {
                "title": "SEO App Title",
                "description": "SEO desc",
                "keywords": ["a", "b"],
                "robots": "index,follow",
                "og_image": "https://x/og.png",
            }
        )
        db.session.add(a)
        db.session.commit()

        app_dir = os.path.join(app.config["INSTALLED_APPS_DIR"], "seoapp")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "app.py"), "w") as f:
            f.write(
                "from flask import Flask\n"
                "app = Flask(__name__)\n"
                '@app.route("/")\n'
                "def home():\n"
                '    return "<html><head><title>Sub</title></head><body>hi</body></html>"\n'
            )

        disp = DynamicAppDispatcherMiddleware(app)
        from werkzeug.test import Client
        from werkzeug.wrappers import Response

        client = Client(disp, Response)
        res = client.get("/apps/seoapp/")
        body = res.get_data(as_text=True)
        assert res.status_code == 200
        assert "SEO App Title" in body
        assert "SEO desc" in body
        assert "index,follow" in body
        assert "https://x/og.png" in body
        # Sub-app's own title preserved (no duplicate injection)
        assert "<title>Sub</title>" in body


def test_seo_injection_noindex_for_auth_app(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        admin = User(email="admin@example.com", name="Admin", role="admin", is_active=True)
        db.session.add(admin)
        db.session.commit()
        admin_token = generate_jwt(admin)

        a = InstalledApp(
            name="Auth App",
            slug="authapp",
            source_type="zip",
            entry_point="app:app",
            requires_auth=True,
            has_web_ui=True,
            is_active=True,
        )
        a.set_seo({"title": "Auth App", "robots": "index,follow"})
        db.session.add(a)
        db.session.commit()

        app_dir = os.path.join(app.config["INSTALLED_APPS_DIR"], "authapp")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "app.py"), "w") as f:
            f.write(
                "from flask import Flask\n"
                "app = Flask(__name__)\n"
                '@app.route("/")\n'
                "def home():\n"
                '    return "<html><head></head><body>hi</body></html>"\n'
            )

        disp = DynamicAppDispatcherMiddleware(app)
        from werkzeug.test import Client
        from werkzeug.wrappers import Response

        client = Client(disp, Response)
        # Authenticate as admin so the sub-app is actually dispatched.
        res = client.get(
            "/apps/authapp/", headers={"Authorization": f"Bearer {admin_token}"}
        )
        body = res.get_data(as_text=True)
        # Auth app forced to noindex,nofollow (seo_auth_apps_noindex default on)
        assert "noindex,nofollow" in body


# ---------- Admin Settings page ----------


def test_settings_page_renders_sections(tmp_path):
    app = _make_app(tmp_path)
    admin_id = _seed_admin(app)
    with app.app_context():
        db.create_all()
        admin = db.session.get(User, admin_id)
        client = app.test_client()
        client.set_cookie(JWT_COOKIE_NAME, generate_jwt(admin))
        res = client.get("/admin/settings")
        assert res.status_code == 200
        body = res.get_data(as_text=True)
        assert "Search Engine Optimization" in body
        assert "Dashboard &amp; Login" in body
        assert "Visibility" in body


def test_settings_save_persists(tmp_path):
    app = _make_app(tmp_path)
    admin_id = _seed_admin(app)
    with app.app_context():
        db.create_all()
        admin = db.session.get(User, admin_id)
        client = app.test_client()
        client.set_cookie(JWT_COOKIE_NAME, generate_jwt(admin))
        res = client.post(
            "/admin/settings",
            data={
                "seo_enabled": "on",
                "seo_portal_title": "My Portal",
                "seo_portal_description": "A portal",
                "seo_portal_keywords": "alpha, beta",
                "seo_portal_canonical_base": "https://example.com",
                "seo_portal_robots": "index,follow",
                "seo_allow_app_override": "on",
                "seo_auth_apps_noindex": "on",
                "seo_sitemap_enabled": "on",
                "dashboard_login_required": "on",
                "dashboard_enabled": "on",
                "dashboard_default_app": "public",
                "visibility_show_auth_apps": "on",
            },
            follow_redirects=True,
        )
        assert res.status_code == 200
        assert get_host_setting("seo_portal_title") == "My Portal"
        assert get_host_setting("seo_portal_keywords") == ["alpha", "beta"]
        assert get_host_setting("dashboard_default_app") == "public"
        assert get_host_setting("visibility_show_auth_apps") is True


# ---------- Dashboard visibility button labels ----------


def test_dashboard_visibility_button_labels(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        admin = User(email="admin@example.com", name="Admin", role="admin", is_active=True)
        member = User(email="member@example.com", name="Member", role="user", is_active=True)
        db.session.add_all([admin, member])
        db.session.commit()

        auth = InstalledApp(
            name="Auth App",
            slug="auth",
            source_type="zip",
            entry_point="app:app",
            requires_auth=True,
            has_web_ui=True,
            is_active=True,
        )
        db.session.add(auth)
        db.session.commit()

        # Dashboard public for this test.
        set_host_setting("dashboard_login_required", False)

        # Logged out -> Login button
        client = app.test_client()
        res = client.get("/dashboard")
        assert ">Login<" in res.get_data(as_text=True)

        # Logged in, no permission -> Permission Required
        client2 = app.test_client()
        client2.set_cookie(JWT_COOKIE_NAME, generate_jwt(member))
        res2 = client2.get("/dashboard")
        assert "Permission Required" in res2.get_data(as_text=True)

        # Grant permission -> Launch
        perm = UserAppPermission(user_id=member.id, app_id=auth.id, can_access=True)
        db.session.add(perm)
        db.session.commit()
        res3 = client2.get("/dashboard")
        assert "Launch" in res3.get_data(as_text=True)

        # Visibility off -> auth app hidden entirely
        set_host_setting("visibility_show_auth_apps", False)
        res4 = client2.get("/dashboard")
        assert "Auth App" not in res4.get_data(as_text=True)
