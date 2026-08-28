"""
Host-level settings for AppManager.

Provides typed get/set helpers backed by the ``HostSetting`` model, plus the
canonical defaults. These control SEO rendering, dashboard/login behavior, and
app visibility on the dashboard listing.

Settings are stored as JSON rows in ``host_settings`` and read through
:func:`get_host_setting` / :func:`get_host_settings`. All settings are optional
and fall back to the defaults in :data:`DEFAULT_HOST_SETTINGS`.
"""

from typing import Any, Dict, Optional

from appmanager.database import db
from appmanager.models import HostSetting

# Canonical defaults for host-level settings. Keys are the setting names used in
# the admin Settings page and read at render time.
DEFAULT_HOST_SETTINGS: Dict[str, Any] = {
    # --- SEO section ---
    "seo_enabled": True,  # master toggle for SEO rendering + injection
    "seo_portal_title": "AppManager",  # host portal <title>
    "seo_portal_description": "Self-hosted Flask sub-app container framework.",
    "seo_portal_keywords": ["appmanager", "flask", "sub-apps"],
    "seo_portal_canonical_base": "",  # e.g. https://example.com
    "seo_portal_og_image": "",
    "seo_portal_robots": "index,follow",
    "seo_allow_app_override": True,  # allow admin per-app SEO override in UI
    "seo_auth_apps_noindex": True,  # requires_auth apps default to noindex
    "seo_sitemap_enabled": True,  # generate /sitemap.xml
    # --- Dashboard / Login section ---
    "dashboard_login_required": True,  # /dashboard and / require auth
    "dashboard_enabled": True,  # dashboard landing page on/off
    "dashboard_default_app": "",  # slug to redirect / to when dashboard off
    # --- Visibility section ---
    "visibility_show_auth_apps": True,  # show requires_auth apps on dashboard grid
}


def get_host_setting(key: str, default: Any = None) -> Any:
    """
    Read a single host setting by key, falling back to ``default`` (or the
    canonical default in :data:`DEFAULT_HOST_SETTINGS` if ``default`` is None).
    """
    row = HostSetting.query.filter_by(key=key).first()
    if row is not None:
        return row.get_value()
    if default is not None:
        return default
    return DEFAULT_HOST_SETTINGS.get(key)


def get_host_settings() -> Dict[str, Any]:
    """
    Read all host settings, merged over the canonical defaults.

    Returns a dict with every key in :data:`DEFAULT_HOST_SETTINGS` populated
    (stored values win over defaults).
    """
    result = dict(DEFAULT_HOST_SETTINGS)
    for row in HostSetting.query.all():
        result[row.key] = row.get_value()
    return result


def set_host_setting(key: str, value: Any) -> HostSetting:
    """
    Upsert a single host setting by key.

    Args:
        key: Setting name (e.g. ``\"seo_enabled\"``).
        value: JSON-serializable value to store.

    Returns:
        The created/updated :class:`HostSetting` row (committed).
    """
    row = HostSetting.query.filter_by(key=key).first()
    if row is None:
        row = HostSetting(key=key)
        db.session.add(row)
    row.set_value(value)
    db.session.commit()
    return row


def set_host_settings(settings: Dict[str, Any]) -> None:
    """
    Upsert multiple host settings in a single transaction.

    Args:
        settings: Mapping of key -> value to store.
    """
    for key, value in settings.items():
        row = HostSetting.query.filter_by(key=key).first()
        if row is None:
            row = HostSetting(key=key)
            db.session.add(row)
        row.set_value(value)
    db.session.commit()


def get_seo_config() -> Dict[str, Any]:
    """
    Convenience: return just the SEO-related host settings.
    """
    all_settings = get_host_settings()
    return {k: v for k, v in all_settings.items() if k.startswith("seo_")}


def get_dashboard_config() -> Dict[str, Any]:
    """
    Convenience: return just the dashboard/login + visibility host settings.
    """
    all_settings = get_host_settings()
    keys = [
        "dashboard_login_required",
        "dashboard_enabled",
        "dashboard_default_app",
        "visibility_show_auth_apps",
    ]
    return {k: all_settings.get(k) for k in keys}


def get_default_app_slug() -> Optional[str]:
    """
    Resolve the slug of the app ``/`` should redirect to.

    Prefers the ``dashboard_default_app`` host setting; falls back to the
    ``is_default`` flag on installed apps.
    """
    configured = get_host_setting("dashboard_default_app", "")
    if configured:
        return configured
    from appmanager.models import InstalledApp

    default_app = InstalledApp.query.filter_by(is_default=True, is_active=True).first()
    return default_app.slug if default_app else None


def build_portal_seo(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build the ``seo`` context dict for host portal pages (``/``, ``/dashboard``).

    Reads the SEO host settings and returns a dict shaped like the manifest
    ``seo`` block so ``base.html``'s ``head_meta`` block can render it. ``extra``
    (e.g. a per-page title) overrides the defaults.

    Returns an empty dict when SEO is disabled.
    """
    if not get_host_setting("seo_enabled", True):
        return {}
    base = get_host_setting("seo_portal_canonical_base", "")
    seo: Dict[str, Any] = {
        "title": get_host_setting("seo_portal_title", "AppManager"),
        "description": get_host_setting("seo_portal_description", ""),
        "keywords": get_host_setting("seo_portal_keywords", []),
        "robots": get_host_setting("seo_portal_robots", "index,follow"),
        "og_type": "website",
    }
    og_image = get_host_setting("seo_portal_og_image", "")
    if og_image:
        seo["og_image"] = og_image
        seo["og_title"] = seo["title"]
        seo["og_description"] = seo["description"]
        seo["twitter_card"] = "summary_large_image"
        seo["twitter_image"] = og_image
    if base:
        seo["canonical_url"] = base.rstrip("/") + "/"
    if extra:
        seo.update({k: v for k, v in extra.items() if v is not None})
    return seo
