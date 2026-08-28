import logging
import os
from typing import Any, List, Optional, Union

from flask import Flask, redirect, render_template, url_for

from appmanager.admin import admin_bp
from appmanager.auth import auth_bp
from appmanager.auth.utils import get_current_user
from appmanager.config import Config
from appmanager.database import db as default_db
from appmanager.database import init_db
from appmanager.extensions import init_extensions
from appmanager.health import check_all_apps_health, check_app_health
from appmanager.middleware import DynamicAppDispatcherMiddleware
from appmanager.models import InstalledApp, UserAppPermission


def _setup_logging(app: Flask) -> None:
    """
    Configures structured logging for the AppManager package.
    """
    level_name = app.config.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)
    log_format = app.config.get(
        "LOG_FORMAT",
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    )

    from appmanager.security import SensitiveDataFilter

    logger = logging.getLogger("appmanager")
    logger.setLevel(log_level)
    sens_filter = SensitiveDataFilter()
    logger.addFilter(sens_filter)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(log_format))
        handler.addFilter(sens_filter)
        logger.addHandler(handler)


class AppManager:
    """
    Standard Flask extension class for AppManager.
    Allows integrating AppManager into existing Flask applications or factory patterns.

    Usage:
        app = Flask(__name__)
        manager = AppManager(app)
        # or
        manager = AppManager()
        manager.init_app(app)
    """

    def __init__(
        self,
        app: Optional[Flask] = None,
        db: Optional[Any] = None,
        register_routes: bool = True,
        register_blueprints: bool = True,
    ) -> None:
        self.app = app
        self.db = db
        self.register_routes = register_routes
        self.register_blueprints = register_blueprints
        self.dispatcher: Optional[DynamicAppDispatcherMiddleware] = None

        if app is not None:
            self.init_app(app, db=db)

    def init_app(self, app: Flask, db: Optional[Any] = None) -> None:
        """
        Initializes the application with AppManager extension.
        """
        # Set default configs if not already set
        for key in dir(Config):
            if key.isupper() and key not in app.config:
                app.config[key] = getattr(Config, key)

        _setup_logging(app)

        # Ensure base directories exist
        base_dir = app.config.get("BASE_DIR", Config.BASE_DIR)
        os.makedirs(app.config["INSTALLED_APPS_DIR"], exist_ok=True)
        os.makedirs(app.config["TEMP_UPLOAD_DIR"], exist_ok=True)
        os.makedirs(os.path.join(base_dir, "instance"), exist_ok=True)

        # Initialize Database & Extension Helpers
        init_db(app)
        init_extensions(app)

        # One-time migration: legacy settings_json → typed app_configs rows
        try:
            from appmanager.app_config import migrate_from_settings_json

            migrate_from_settings_json()
        except Exception:
            pass

        from appmanager.security import generate_csrf_token

        app.jinja_env.globals["csrf_token"] = generate_csrf_token

        # Register Blueprints
        if self.register_blueprints:
            from appmanager.api import api_bp

            app.register_blueprint(auth_bp)

            # Mount extension-declared admin blueprints onto admin_bp BEFORE it
            # is registered on the app (admin auth enforced at mount time).
            # Needs an app context for DB/manifest lookups.
            try:
                from appmanager.admin.registry import mount_all_app_admin_blueprints

                with app.app_context():
                    mount_all_app_admin_blueprints(admin_bp)
            except Exception:
                pass

            app.register_blueprint(admin_bp)
            app.register_blueprint(api_bp)

        # Register Default Landing & Error Handlers if requested
        if self.register_routes:
            self._register_default_routes(app)

        # Store extension instance on the Flask app
        if not hasattr(app, "extensions"):
            app.extensions = {}
        app.extensions["appmanager"] = self

    def _register_default_routes(self, app: Flask) -> None:
        @app.route("/")
        def index():
            from appmanager.host_settings import (
                get_default_app_slug,
                get_host_setting,
            )

            # If the dashboard is disabled, redirect to the configured default app.
            if not get_host_setting("dashboard_enabled", True):
                default_slug = get_default_app_slug()
                if default_slug:
                    return redirect(f"/apps/{default_slug}/")

            default_app = InstalledApp.query.filter_by(is_default=True, is_active=True).first()
            if default_app:
                if not default_app.requires_auth:
                    return redirect(f"/apps/{default_app.slug}/")
                user = get_current_user()
                if not user:
                    return redirect(url_for("auth.login", next=f"/apps/{default_app.slug}/"))
                return redirect(f"/apps/{default_app.slug}/")
            return redirect(url_for("dashboard"))

        @app.route("/dashboard")
        def dashboard():
            from appmanager.host_settings import (
                build_portal_seo,
                get_host_setting,
            )

            user = get_current_user()
            login_required = get_host_setting("dashboard_login_required", True)
            if login_required and not user:
                return redirect(url_for("auth.login"))

            if user and user.is_admin():
                accessible_apps = InstalledApp.query.filter_by(is_active=True).all()
            elif user:
                # Logged-in non-admin: apps the user can access, plus (when visibility
                # is on) auth apps they lack permission for — shown with a
                # "Permission Required" button.
                permitted = (
                    default_db.session.query(InstalledApp)
                    .join(UserAppPermission, UserAppPermission.app_id == InstalledApp.id)
                    .filter(
                        UserAppPermission.user_id == user.id,
                        UserAppPermission.can_access.is_(True),
                        InstalledApp.is_active.is_(True),
                    )
                    .all()
                )
                if get_host_setting("visibility_show_auth_apps", True):
                    all_active = InstalledApp.query.filter_by(is_active=True).all()
                    permitted_ids = {a.id for a in permitted}
                    accessible_apps = permitted + [
                        a for a in all_active if a.id not in permitted_ids
                    ]
                else:
                    accessible_apps = permitted
            else:
                # Logged out: show public apps (auth apps appear only if visibility is on,
                # with a "Login" button).
                accessible_apps = InstalledApp.query.filter_by(
                    is_active=True, requires_auth=False
                ).all()
                if get_host_setting("visibility_show_auth_apps", True):
                    auth_apps = InstalledApp.query.filter_by(
                        is_active=True, requires_auth=True
                    ).all()
                    accessible_apps = accessible_apps + auth_apps

            # Visibility: optionally hide requires_auth apps from the listing.
            show_auth_apps = get_host_setting("visibility_show_auth_apps", True)
            if not show_auth_apps:
                accessible_apps = [a for a in accessible_apps if not a.requires_auth]

            # Per-app launch state for the visibility button labels.
            app_states = {}
            for a in accessible_apps:
                if not a.requires_auth:
                    app_states[a.slug] = "launch"
                elif not user:
                    app_states[a.slug] = "login"
                elif user.is_admin():
                    app_states[a.slug] = "launch"
                else:
                    perm = UserAppPermission.query.filter_by(
                        user_id=user.id, app_id=a.id
                    ).first()
                    app_states[a.slug] = "launch" if (perm and perm.can_access) else "permission"

            seo = build_portal_seo()
            return render_template(
                "index.html",
                user=user,
                apps=accessible_apps,
                app_states=app_states,
                seo=seo,
            )

        @app.route("/robots.txt")
        def robots_txt():
            from flask import Response

            from appmanager.host_settings import get_host_setting

            lines = ["User-agent: *", "Allow: /"]
            if get_host_setting("seo_sitemap_enabled", True):
                base = get_host_setting("seo_portal_canonical_base", "")
                if base:
                    lines.append(f"Sitemap: {base.rstrip('/')}/sitemap.xml")
            return Response("\n".join(lines) + "\n", mimetype="text/plain")

        @app.route("/sitemap.xml")
        def sitemap_xml():
            from flask import Response

            from appmanager.host_settings import get_host_setting

            if not get_host_setting("seo_sitemap_enabled", True):
                return Response("", status=404, mimetype="application/xml")
            base = get_host_setting("seo_portal_canonical_base", "")
            if not base:
                return Response("", status=404, mimetype="application/xml")
            base = base.rstrip("/")
            urls = [f"{base}/"]
            for a in InstalledApp.query.filter_by(
                is_active=True, has_web_ui=True, requires_auth=False
            ).all():
                urls.append(f"{base}/apps/{a.slug}/")
            urlset = "\n".join(
                f"  <url><loc>{u}</loc></url>" for u in urls
            )
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"{urlset}\n"
                "</urlset>\n"
            )
            return Response(xml, mimetype="application/xml")

        @app.errorhandler(404)
        def not_found_error(error):
            return render_template("errors/404.html", user=get_current_user()), 404

        @app.errorhandler(500)
        def internal_error(error):
            return render_template("errors/500.html", user=get_current_user()), 500

        @app.errorhandler(403)
        def forbidden_error(error):
            return render_template("errors/403.html", user=get_current_user()), 403

        @app.errorhandler(401)
        def unauthorized_error(error):
            return render_template("errors/401.html", user=get_current_user()), 401

    def create_dispatcher(self, app: Optional[Flask] = None) -> DynamicAppDispatcherMiddleware:
        """
        Wraps the Flask app with DynamicAppDispatcherMiddleware for sub-app routing.
        """
        target_app = app or self.app
        if target_app is None:
            raise ValueError("Flask app instance must be provided to create_dispatcher.")
        self.dispatcher = DynamicAppDispatcherMiddleware(target_app)
        return self.dispatcher

    def clear_cache(self, slug: Optional[str] = None) -> None:
        """
        Clears the in-memory WSGI cache for a specific sub-app or all sub-apps.
        """
        if self.dispatcher:
            self.dispatcher.clear_cache(slug=slug)

    def get_app(self, slug: str) -> Optional[InstalledApp]:
        """
        Retrieve InstalledApp model instance by slug.
        """
        return InstalledApp.query.filter_by(slug=slug).first()

    def get_apps(self, active_only: bool = True) -> List[InstalledApp]:
        """
        List installed sub-apps.
        """
        query = InstalledApp.query
        if active_only:
            query = query.filter_by(is_active=True)
        return query.all()

    def check_health(self, slug: Optional[str] = None) -> Union[List[Any], Any]:
        """
        Runs health check for a single sub-app by slug, or all active sub-apps if slug is None.
        """
        if slug:
            app_record = self.get_app(slug)
            if not app_record:
                raise ValueError(f"App with slug '{slug}' not found.")
            return check_app_health(app_record)
        return check_all_apps_health()
