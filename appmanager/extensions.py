import json
import os
import sys
from datetime import datetime, timezone

from markupsafe import Markup

from appmanager.database import db
from appmanager.hooks import hooks, render_slot
from appmanager.models import AppExtensionData, InstalledApp


def get_active_extensions(target_app_slug="appmanager"):
    """
    Returns active extension apps extending a specific target app.
    """
    return InstalledApp.query.filter_by(
        app_type="extension", target_app=target_app_slug, is_active=True
    ).all()


def get_extension_data(extension_slug, entity_type, entity_id):
    """
    Retrieves stored JSON data dictionary for a specific extension & entity.
    """
    record = AppExtensionData.query.filter_by(
        extension_slug=extension_slug, entity_type=entity_type, entity_id=entity_id
    ).first()
    if record and record.data_json:
        try:
            return json.loads(record.data_json)
        except Exception:
            return None
    return None


def set_extension_data(extension_slug, entity_type, entity_id, data_dict):
    """
    Stores or updates JSON data for a specific extension & entity.
    """
    record = AppExtensionData.query.filter_by(
        extension_slug=extension_slug, entity_type=entity_type, entity_id=entity_id
    ).first()
    payload = json.dumps(data_dict) if data_dict is not None else None
    if not record:
        record = AppExtensionData(
            extension_slug=extension_slug,
            entity_type=entity_type,
            entity_id=entity_id,
            data_json=payload,
        )
        db.session.add(record)
    else:
        record.data_json = payload
        record.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return record


DEFAULT_PRESET_FLAIRS = [
    {"title": "🚀 Lead Developer", "color": "#38bdf8"},
    {"title": "⭐ VIP Member", "color": "#facc15"},
    {"title": "🛡️ Moderator", "color": "#4ade80"},
    {"title": "🔥 Core Contributor", "color": "#f87171"},
    {"title": "💎 Sponsor", "color": "#c084fc"},
]


def _flairs_extension():
    """
    Import the User Flairs extension module if it is installed and active.
    Returns None (graceful fallback) if unavailable.
    """
    try:
        from appmanager.models import InstalledApp

        rec = InstalledApp.query.filter_by(slug="extension-flairs", is_active=True).first()
        if not rec:
            return None
        return _load_extension_module("extension-flairs", "extension")
    except Exception:
        return None


def _load_extension_module(slug, module_name, installed_apps_dir=None):
    """
    Load an extension's module from its installed_apps directory.
    Adds the extension dir to sys.path (idempotent) so top-level imports
    like `import extension` / `import flairs_admin` resolve.
    Returns the module, or None on failure.
    """
    try:
        base = installed_apps_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "installed_apps"
        )
        ext_dir = os.path.join(base, slug)
        if ext_dir not in sys.path:
            sys.path.insert(0, ext_dir)
        import importlib

        return importlib.import_module(module_name)
    except Exception:
        return None


def _register_extension_templates(app, slug):
    """
    Add an extension's `templates/` dir to the app's Jinja search path so its
    blueprints can render their own templates. Idempotent; graceful on failure.
    """
    try:
        base = app.config.get("INSTALLED_APPS_DIR")
        tdir = os.path.join(base, slug, "templates")
        if not os.path.isdir(tdir):
            return
        from jinja2 import ChoiceLoader, FileSystemLoader

        loader = app.jinja_env.loader
        if isinstance(loader, ChoiceLoader):
            paths = [ldr.searchpath for ldr in loader.loaders if isinstance(ldr, FileSystemLoader)]
            if tdir not in paths:
                loader.loaders.append(FileSystemLoader(tdir))
        else:
            app.jinja_env.loader = ChoiceLoader([loader, FileSystemLoader(tdir)])
    except Exception:
        pass


def get_user_flair(user_id):
    """
    Retrieves custom flair title and color for a user. Delegates to the
    User Flairs extension if present; falls back to AppExtensionData.
    """
    mod = _flairs_extension()
    if mod is not None and hasattr(mod, "get_user_flair"):
        try:
            return mod.get_user_flair(user_id)
        except Exception:
            pass
    return get_extension_data("extension-flairs", "user", user_id)


def set_user_flair(user_id, title, color="#a855f7"):
    """
    Sets or updates custom flair for a user. Delegates to the extension if present;
    falls back to AppExtensionData.
    """
    mod = _flairs_extension()
    if mod is not None and hasattr(mod, "set_user_flair"):
        try:
            return mod.set_user_flair(user_id, title, color)
        except Exception:
            pass
    return set_extension_data("extension-flairs", "user", user_id, {"title": title, "color": color})


def render_user_flair_badge(user_id):
    """
    Jinja helper rendering styled HTML flair badge for a user. Delegates to
    the extension if present; falls back to AppExtensionData rendering.
    """
    mod = _flairs_extension()
    if mod is not None and hasattr(mod, "render_user_flair_badge"):
        try:
            return mod.render_user_flair_badge(user_id)
        except Exception:
            pass
    flair = get_user_flair(user_id)
    if flair and flair.get("title"):
        title = flair.get("title")
        color = flair.get("color", "#38bdf8")
        return Markup(
            f'<span class="badge" style="background-color: {color}22; color: {color}; border: 1px solid {color}44; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; font-weight: 500; display: inline-flex; align-items: center; gap: 4px; margin-left: 6px;">'
            f"{title}"
            f"</span>"
        )
    return Markup("")


def render_sparkline(health_logs):
    """
    Jinja helper rendering a braille sparkline from a list of AppHealthLog rows.
    Each log maps to a braille char: healthy=high, degraded=mid, down=low.
    """
    if not health_logs:
        return Markup("")
    # Braille chars: low / mid / high
    chars = {"healthy": "⣿", "degraded": "⣤", "down": "⣀"}
    out = []
    for log in health_logs:
        status = getattr(log, "status", "") or ""
        out.append(chars.get(status, "·"))
    return Markup(
        '<span class="sparkline" title="Health history (oldest → newest)">'
        + "".join(out)
        + "</span>"
    )


def init_extensions(app):
    """
    Registers Jinja helper functions and hook/slot integrations for extensions.
    """
    # Ensure the User Flairs extension dir is importable, then load it and
    # let it register its own slots (user_badge). Graceful if unavailable.
    mod = _load_extension_module(
        "extension-flairs", "extension", app.config.get("INSTALLED_APPS_DIR")
    )
    if mod is not None and hasattr(mod, "register_slots"):
        try:
            mod.register_slots()
        except Exception as e:
            print(f"[EXTENSION] Failed to register slots for extension-flairs: {e}")

    # Add the extension's templates dir to the app's template search path so
    # its admin blueprint can render its own templates.
    _register_extension_templates(app, "extension-flairs")

    app.jinja_env.globals["get_user_flair"] = get_user_flair
    app.jinja_env.globals["render_user_flair_badge"] = render_user_flair_badge
    app.jinja_env.globals["get_active_extensions"] = get_active_extensions
    app.jinja_env.globals["DEFAULT_PRESET_FLAIRS"] = DEFAULT_PRESET_FLAIRS
    app.jinja_env.globals["render_slot"] = render_slot
    app.jinja_env.globals["render_sparkline"] = render_sparkline
    app.jinja_env.globals["hooks"] = hooks
