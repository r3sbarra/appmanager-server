import logging
import os
import re
from typing import Any, Callable, Dict, Optional

import jwt
from flask import Flask

from appmanager.admin.app_installer import load_wsgi_app_from_path
from appmanager.database import db
from appmanager.models import InstalledApp, User, UserAppPermission
from appmanager.signals import subapp_reloaded

logger = logging.getLogger("appmanager.dispatcher")
VALID_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class DynamicAppDispatcherMiddleware:
    """
    WSGI middleware for dynamically dispatching requests to installed sub-apps under `/apps/<slug>/*`.
    Provides namespace isolation, user header propagation, and in-memory callable caching.
    """

    def __init__(self, main_app: Flask) -> None:
        self.main_app = main_app
        self.sub_app_cache: Dict[str, Callable] = {}

    def clear_cache(self, slug: Optional[str] = None) -> None:
        """
        Clears the in-memory cache for a single sub-app or all sub-apps.
        """
        if slug:
            self.sub_app_cache.pop(slug, None)
        else:
            self.sub_app_cache.clear()

    def _get_user_from_environ(
        self, environ: Dict[str, Any], app_context_app: Flask
    ) -> Optional[User]:
        """
        Extracts user and checks auth from WSGI request environ
        """
        token = None
        cookie_header = environ.get("HTTP_COOKIE", "")
        if cookie_header:
            from http.cookies import SimpleCookie

            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
                if "appmanager_jwt" in cookie:
                    token = cookie["appmanager_jwt"].value
            except Exception:
                pass

        if not token:
            auth_header = environ.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

        if not token:
            return None

        try:
            jwt_secret = app_context_app.config["JWT_SECRET"]
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            user_id = payload.get("user_id")
            user = db.session.get(User, user_id)
            if user and user.is_active:
                return user
        except Exception:
            return None

        return None

    def _send_html_response(
        self,
        start_response: Callable,
        status_code: int,
        title: str,
        message: str,
        link_url: Optional[str] = None,
        link_text: Optional[str] = None,
    ):
        status_names = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Internal Server Error",
        }
        status = f"{status_code} " + status_names.get(status_code, "Error")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "SAMEORIGIN"),
        ]
        code_color = (
            "#6366f1"
            if status_code == 401
            else "#ef4444"
            if status_code in (403, 404)
            else "#f59e0b"
        )
        link_html = (
            f'<p><a href="{link_url}" style="background-color:#6366f1;color:white;padding:10px 20px;text-decoration:none;border-radius:8px;font-weight:500;display:inline-block;transition:all 0.2s;">{link_text}</a></p>'
            if link_url
            else ""
        )
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title} - AppManager</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }}
        .card {{ background: #1e293b; padding: 40px; border-radius: 16px; max-width: 480px; width: 100%; text-align: center; border: 1px solid #334155; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }}
        .code {{ font-size: 64px; font-weight: 800; color: {code_color}; margin-bottom: 8px; line-height: 1; }}
        h1 {{ color: #f8fafc; font-size: 22px; font-weight: 700; margin-top: 0; margin-bottom: 12px; }}
        p {{ color: #94a3b8; line-height: 1.6; font-size: 14px; margin-bottom: 24px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="code">{status_code}</div>
        <h1>{title}</h1>
        <p>{message}</p>
        {link_html}
    </div>
</body>
</html>"""
        start_response(status, headers)
        return [html.encode("utf-8")]

    def __call__(self, environ: Dict[str, Any], start_response: Callable):
        # 1. Strip any client-supplied spoofed AppManager internal headers from incoming environ
        for header_key in [k for k in environ.keys() if k.startswith("HTTP_X_APPMANAGER_")]:
            del environ[header_key]

        path_info = environ.get("PATH_INFO", "")

        # Check if the request is targeting an installed sub-app (/apps/<slug>/...)
        if path_info.startswith("/apps/"):
            parts = path_info.strip("/").split("/")
            if len(parts) >= 2:
                slug = parts[1]

                # Validate slug to prevent path traversal
                if not VALID_SLUG_PATTERN.match(slug):
                    return self._send_html_response(
                        start_response,
                        400,
                        "Invalid App Identifier",
                        "The requested app path contains invalid characters.",
                        link_url="/",
                        link_text="Return to Home",
                    )

                # Use main app context to query database
                with self.main_app.app_context():
                    app_record = InstalledApp.query.filter_by(slug=slug, is_active=True).first()
                    if not app_record:
                        return self._send_html_response(
                            start_response,
                            404,
                            "App Not Found",
                            f"No active Flask app is installed at path '/apps/{slug}'.",
                            link_url="/",
                            link_text="Return to Home",
                        )

                    # 1. Authenticate User & Check Permission
                    user = self._get_user_from_environ(environ, self.main_app)
                    if app_record.requires_auth:
                        if not user:
                            redirect_path = f"/auth/login?next=/apps/{slug}"
                            return self._send_html_response(
                                start_response,
                                401,
                                "Authentication Required",
                                f"You must be logged into AppManager to access '{app_record.name}'.",
                                link_url=redirect_path,
                                link_text="Log In to Continue",
                            )

                        if not user.is_admin():
                            perm = UserAppPermission.query.filter_by(
                                user_id=user.id, app_id=app_record.id
                            ).first()
                            if not perm or not perm.can_access:
                                return self._send_html_response(
                                    start_response,
                                    403,
                                    "Access Denied",
                                    f"You do not have permission to access the '{app_record.name}' application. Contact your administrator.",
                                    link_url="/auth/profile",
                                    link_text="Go to My Profile",
                                )

                    # 2. Inject Verified User & Sub-App Context Headers (Guaranteed non-spoofed)
                    if user:
                        environ["HTTP_X_APPMANAGER_USER_ID"] = str(user.id)
                        environ["HTTP_X_APPMANAGER_USER_EMAIL"] = user.email
                        environ["HTTP_X_APPMANAGER_USER_ROLE"] = user.role
                    environ["HTTP_X_APPMANAGER_SUBAPP_SLUG"] = slug
                    environ["HTTP_X_FORWARDED_PREFIX"] = f"/apps/{slug}"

                    # 3. Inject the app's stored per-app API key so the sub-app can
                    #    authenticate to the host REST API without holding credentials
                    #    itself. (Validation happens in the host API decorator.)
                    try:
                        from appmanager.app_config import get_app_api_key

                        stored_key = get_app_api_key(app_record.id)
                        if stored_key:
                            environ["HTTP_X_APPMANAGER_APP_KEY"] = stored_key
                    except Exception as e:
                        logger.warning("App key injection failed for '%s': %s", slug, e)

                    logger.debug(
                        "Dispatching request '%s %s' to sub-app '%s' (user: %s)",
                        environ.get("REQUEST_METHOD", "GET"),
                        path_info,
                        slug,
                        user.email if user else "anonymous",
                    )

                    # 3. Load or Retrieve Sub-App WSGI Instance
                    app_dir = os.path.join(
                        self.main_app.config["INSTALLED_APPS_DIR"], app_record.slug
                    )
                    if not os.path.exists(app_dir):
                        logger.warning("Sub-app directory for '%s' not found at %s", slug, app_dir)
                        is_admin = bool(user and getattr(user, "is_admin", False))
                        debug_mode = bool(self.main_app.config.get("DEBUG", False))
                        if is_admin or debug_mode:
                            msg = f"App directory for '{app_record.name}' was not found on the server filesystem."
                        else:
                            msg = f"The '{app_record.name}' application is currently unavailable."
                        return self._send_html_response(
                            start_response,
                            500,
                            "Application Unavailable",
                            msg,
                            link_url="/",
                            link_text="Return to Home",
                        )

                    try:
                        if slug not in self.sub_app_cache:
                            logger.info(
                                "Loading sub-app '%s' into WSGI cache (entrypoint: %s)",
                                slug,
                                app_record.entry_point,
                            )
                            sub_app_obj = load_wsgi_app_from_path(app_dir, app_record.entry_point)
                            # Get standard wsgi_app callable if it's a Flask instance
                            wsgi_callable = getattr(sub_app_obj, "wsgi_app", sub_app_obj)
                            self.sub_app_cache[slug] = wsgi_callable
                            try:
                                subapp_reloaded.send(self, slug=slug)
                            except Exception:
                                pass

                        wsgi_callable = self.sub_app_cache[slug]
                    except Exception as e:
                        import logging

                        logging.getLogger("appmanager.dispatcher").exception(
                            f"Error loading sub-app '{app_record.slug}': {e}"
                        )
                        is_admin = bool(user and getattr(user, "is_admin", False))
                        debug_mode = bool(self.main_app.config.get("DEBUG", False))
                        if is_admin or debug_mode:
                            err_msg = f"Failed to load Flask sub-app '{app_record.name}': {str(e)}"
                        else:
                            err_msg = f"The '{app_record.name}' application encountered an error while starting. Please contact an administrator."

                        return self._send_html_response(
                            start_response,
                            500,
                            "Application Error",
                            err_msg,
                            link_url="/",
                            link_text="Return to Home",
                        )

                    # Build SEO tags inside the app context (needs DB + host settings).
                    seo_tags = self._build_seo_tags(app_record, user)

                # Rewrite SCRIPT_NAME and PATH_INFO for WSGI sub-app routing
                prefix = f"/apps/{slug}"
                environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + prefix
                new_path_info = path_info[len(prefix) :]
                if not new_path_info:
                    new_path_info = "/"
                environ["PATH_INFO"] = new_path_info

                # Wrap the sub-app response to inject SEO metadata into HTML.
                if seo_tags:
                    return self._dispatch_with_seo(
                        wsgi_callable, environ, start_response, seo_tags
                    )

                return wsgi_callable(environ, start_response)

        # Fall through to master AppManager Flask app
        return self.main_app(environ, start_response)

    def _build_seo_tags(self, app_record, user) -> str:
        """
        Build the HTML string of SEO tags to inject into a sub-app's ``<head>``.

        Returns an empty string when SEO is disabled or the app declares no SEO
        metadata. Auth-required apps get ``noindex`` forced when the
        ``seo_auth_apps_noindex`` host setting is on.
        """
        try:
            from appmanager.host_settings import get_host_setting
        except Exception:
            return ""

        if not get_host_setting("seo_enabled", True):
            return ""

        seo = app_record.get_seo()
        if not seo:
            return ""

        tags = []
        title = seo.get("title") or app_record.name
        if title:
            tags.append(f"<title>{self._esc(title)}</title>")
        if seo.get("description"):
            tags.append(f'<meta name="description" content="{self._esc(seo["description"])}">')
        if seo.get("keywords"):
            kw = ", ".join(seo["keywords"])
            tags.append(f'<meta name="keywords" content="{self._esc(kw)}">')
        if seo.get("canonical_url"):
            tags.append(f'<link rel="canonical" href="{self._esc(seo["canonical_url"])}">')
        if seo.get("og_image"):
            tags.append(f'<meta property="og:image" content="{self._esc(seo["og_image"])}">')
            tags.append(f'<meta property="og:title" content="{self._esc(title)}">')
            tags.append('<meta name="twitter:card" content="summary_large_image">')
            tags.append(f'<meta name="twitter:image" content="{self._esc(seo["og_image"])}">')

        # Robots: force noindex for auth-required apps when configured.
        robots = seo.get("robots")
        if app_record.requires_auth and get_host_setting("seo_auth_apps_noindex", True):
            robots = "noindex,nofollow"
        if robots:
            tags.append(f'<meta name="robots" content="{self._esc(robots)}">')

        if seo.get("json_ld"):
            tags.append(f'<script type="application/ld+json">{seo["json_ld"]}</script>')

        return "\n    ".join(tags)

    @staticmethod
    def _esc(value: str) -> str:
        """Minimal HTML-escape for attribute/text injection."""
        return (
            str(value)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _dispatch_with_seo(self, wsgi_callable, environ, start_response, seo_tags: str):
        """
        Call the sub-app, capture its HTML response, and inject SEO tags into
        ``<head>`` (only for ``text/html`` responses, only when the tag isn't
        already present).
        """
        captured_headers = {}
        status_holder = {}

        def wrapped_start_response(status, headers, exc_info=None):
            status_holder["status"] = status
            for k, v in headers:
                captured_headers.setdefault(k.lower(), v)
            return start_response(status, headers, exc_info)

        body_chunks = []
        try:
            for chunk in wsgi_callable(environ, wrapped_start_response):
                body_chunks.append(chunk)
        except Exception:
            # Fall back to the raw callable if wrapping fails.
            return wsgi_callable(environ, start_response)

        content_type = captured_headers.get("content-type", "")
        if "text/html" in content_type:
            body = b"".join(body_chunks)
            try:
                text = body.decode("utf-8")
            except Exception:
                text = body.decode("latin-1")
            injected = self._inject_into_head(text, seo_tags)
            if injected != text:
                new_body = injected.encode("utf-8")
                # Rebuild headers, updating Content-Length.
                new_headers = []
                for k, v in captured_headers.items():
                    if k == "content-length":
                        continue
                    new_headers.append((k, v))
                new_headers.append(("Content-Length", str(len(new_body))))
                start_response(status_holder["status"], new_headers)
                return [new_body]

        # No injection needed; replay the original response.
        start_response(status_holder["status"], list(captured_headers.items()))
        return body_chunks

    @staticmethod
    def _inject_into_head(html: str, seo_tags: str) -> str:
        """
        Insert ``seo_tags`` right after the opening ``<head>`` tag, skipping any
        tag that is already present in the document (avoid duplicates).
        """
        if not seo_tags:
            return html
        head_match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
        if not head_match:
            return html
        # Skip tags already present.
        to_inject = []
        for tag in seo_tags.split("\n    "):
            marker = tag.split(" ")[0].lstrip("<")  # e.g. "title", "meta", "link"
            if marker == "meta":
                # Skip if a meta with the same name/property already exists.
                name_match = re.search(r'(?:name|property)="([^"]+)"', tag)
                if name_match:
                    attr = name_match.group(1)
                    if re.search(
                        rf'(?:name|property)="{re.escape(attr)}"', html, re.IGNORECASE
                    ):
                        continue
            elif marker == "title":
                if re.search(r"<title[^>]*>", html, re.IGNORECASE):
                    continue
            elif marker == "link":
                if re.search(r'<link[^>]*rel="canonical"', html, re.IGNORECASE):
                    continue
            elif marker == "script":
                if re.search(r'<script[^>]*application/ld\+json', html, re.IGNORECASE):
                    continue
            to_inject.append(tag)
        if not to_inject:
            return html
        insert_at = head_match.end()
        return html[:insert_at] + "\n    " + "\n    ".join(to_inject) + html[insert_at:]
