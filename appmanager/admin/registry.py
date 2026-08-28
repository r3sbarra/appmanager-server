"""
Extension admin registry — the manifest contract.

Each installed app can declare its own admin surface in its `manifest.json`:

    {
      "name": "User Flairs",
      "slug": "user-flairs",
      "type": "extension",
      "admin_sections": [
        {"id": "presets", "label": "Flair presets", "icon": "tag",
         "blueprint": "flairs_admin:presets", "order": 10}
      ],
      "settings_schema": [
        {"key": "max_flairs", "type": "integer", "label": "Max flairs per member", "default": 3}
      ]
    }

Two mechanisms:
  * `settings_schema`  → the framework renders a generated typed form (zero code).
  * `admin_sections`   → the extension ships its own Flask blueprint; the framework
                         mounts it at `/admin/apps/<slug>/<panel>` with admin auth
                         enforced at mount time (never trusted to the extension).

The registry persists declared panels to `app_admin_panels` on install/upgrade
and provides a kill switch so a broken panel never takes down the rest of /admin.
"""

import importlib
import json
import os
import traceback

from flask import Blueprint, current_app, redirect, request, url_for

from appmanager.database import db
from appmanager.models import AppAdminPanel, InstalledApp

# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def parse_manifest(app_dir):
    """Parse manifest.json from an installed app's directory."""
    manifest_path = os.path.join(app_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def app_dir_for(app_record):
    """Resolve the on-disk directory for an installed app."""
    base = current_app.config.get("INSTALLED_APPS_DIR")
    return os.path.join(base, app_record.slug) if base else None


def manifest_for(app_record):
    """Fetch the manifest dict for an installed app (empty if none)."""
    d = app_dir_for(app_record)
    if not d:
        return {}
    return parse_manifest(d)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def sync_panels(app_record, manifest=None):
    """
    Reconcile `app_admin_panels` rows with the app's manifest. Called on
    install and on upgrade/reload. Returns the list of panel dicts.
    """
    manifest = manifest or manifest_for(app_record)
    declared = manifest.get("admin_sections", []) or []
    # Remove panels no longer declared
    declared_ids = {p.get("id") for p in declared if p.get("id")}
    AppAdminPanel.query.filter_by(app_id=app_record.id).filter(
        ~AppAdminPanel.panel_id.in_(declared_ids) if declared_ids else True
    ).delete(synchronize_session=False)

    for i, p in enumerate(declared):
        pid = p.get("id")
        if not pid:
            continue
        row = AppAdminPanel.query.filter_by(app_id=app_record.id, panel_id=pid).first()
        if not row:
            row = AppAdminPanel(app_id=app_record.id, panel_id=pid)
            db.session.add(row)
        row.label = p.get("label", pid)
        row.icon = p.get("icon")
        row.endpoint = p.get("blueprint")
        row.sort_order = p.get("order", i * 10)
    db.session.commit()
    return [
        p.to_dict()
        for p in AppAdminPanel.query.filter_by(app_id=app_record.id)
        .order_by(AppAdminPanel.sort_order)
        .all()
    ]


def panels_for(app_record):
    """Ordered list of panel dicts for an app."""
    rows = (
        AppAdminPanel.query.filter_by(app_id=app_record.id).order_by(AppAdminPanel.sort_order).all()
    )
    return [r.to_dict() for r in rows]


def settings_schema_for(app_record):
    """The app's declared settings_schema (list of descriptors)."""
    manifest = manifest_for(app_record)
    schema = manifest.get("settings_schema", []) or []
    return schema if isinstance(schema, list) else []


# ---------------------------------------------------------------------------
# Blueprint mounting (with admin auth enforced at mount time)
# ---------------------------------------------------------------------------


def _resolve_blueprint(endpoint_ref):
    """
    Resolve 'module:blueprint_var' or 'module:blueprint_var:endpoint' to a
    (blueprint, endpoint_name) tuple. Returns (None, None) on any failure.
    """
    if not endpoint_ref:
        return None, None
    parts = endpoint_ref.split(":")
    if len(parts) < 2:
        return None, None
    module_path, attr = parts[0], parts[1]
    endpoint = parts[2] if len(parts) > 2 else None
    try:
        module = importlib.import_module(module_path)
        bp = getattr(module, attr)
        if not isinstance(bp, Blueprint):
            return None, None
        return bp, endpoint
    except Exception:
        return None, None


def mount_app_admin_blueprints(app_record, admin_bp):
    """
    Mount an app's declared admin blueprints under `/admin/apps/<slug>/<panel>`.
    Admin auth is enforced via a before_request on the mounted blueprint — the
    extension is never trusted to self-guard. Returns (mounted, errors).
    """
    from appmanager.auth.utils import get_current_user

    def _admin_guard():
        """before_request guard: 401/redirect unless the caller is an admin."""
        from flask import request

        user = get_current_user()
        if not user:
            if request.is_json or request.path.startswith("/api/"):
                return {"error": "Authentication required"}, 401
            return redirect(url_for("auth.login", next=request.url))
        if not user.is_admin():
            if request.is_json or request.path.startswith("/api/"):
                return {"error": "Admin privilege required"}, 403
            return redirect(url_for("auth.profile"))
        return None

    mounted = []
    errors = []
    for panel in panels_for(app_record):
        if not panel.get("endpoint"):
            continue  # generated-form panel, no blueprint to mount
        bp, endpoint = _resolve_blueprint(panel["endpoint"])
        if bp is None:
            errors.append(
                f"Panel '{panel['panel_id']}': could not resolve blueprint '{panel['endpoint']}'"
            )
            continue

        # Namespace the blueprint under /admin/apps/<slug>/<panel_id>
        # (admin_bp already carries the /admin prefix, so nest under /apps/...)
        url_prefix = f"/apps/{app_record.slug}/{panel['panel_id']}"
        bp.url_prefix = url_prefix
        # Enforce admin auth at mount time — non-negotiable
        bp.before_request(_admin_guard)
        try:
            admin_bp.register_blueprint(bp)
            mounted.append(panel["panel_id"])
        except Exception as e:
            errors.append(f"Panel '{panel['panel_id']}': mount failed: {e}")
    return mounted, errors


def mount_all_app_admin_blueprints(admin_bp):
    """
    Mount admin blueprints for every installed app that declares them.
    Called once at startup. Failures are logged, never fatal.
    """
    apps = InstalledApp.query.all()
    for a in apps:
        try:
            # Reconcile declared panels with the manifest on every startup so
            # manifest edits are picked up without a manual reinstall.
            sync_panels(a)
            mount_app_admin_blueprints(a, admin_bp)
        except Exception as e:
            print(f"[ADMIN REGISTRY] Failed to mount panels for '{a.slug}': {e}")


# ---------------------------------------------------------------------------
# Generated settings form (settings_schema → form)
# ---------------------------------------------------------------------------


def render_settings_form(app_record, configs):
    """
    Render a generated settings form from the app's settings_schema.
    Returns HTML string, or None if the app has no schema.
    """
    schema = settings_schema_for(app_record)
    if not schema:
        return None
    from markupsafe import Markup

    from appmanager.app_config import get_configs

    configs = configs or get_configs(app_record.id)
    rows = []
    for desc in schema:
        key = desc.get("key")
        if not key:
            continue
        label = desc.get("label", key)
        vtype = desc.get("type", "json")
        value = configs.get(key, desc.get("default"))
        rows.append(_schema_field(key, label, vtype, value, desc))
    body = "\n".join(rows)
    return Markup(
        f'<form method="POST" action="/admin/apps/{app_record.slug}/settings" class="schema-form">'
        f'<input type="hidden" name="csrf_token" value="{_csrf()}">'
        f"{body}"
        f'<div class="action-group mt-2"><button type="submit" class="btn btn-primary btn-sm">Save Settings</button></div>'
        f"</form>"
    )


def _csrf():
    try:
        from appmanager.security import generate_csrf_token

        return generate_csrf_token()
    except Exception:
        return ""


def _schema_field(key, label, vtype, value, desc):
    """Render a single schema field as HTML."""
    if vtype == "boolean":
        checked = " checked" if value else ""
        return (
            f'<div class="form-group"><label class="form-label">{label}</label>'
            f'<label class="flex-center" style="gap:0.5rem;cursor:pointer;">'
            f'<input type="checkbox" name="cfg_{key}" value="1"{checked} class="perm-toggle">'
            f'<span class="text-muted" style="font-size:0.8rem;">enabled</span></label></div>'
        )
    if vtype == "color":
        val = value or "#F5A524"
        return (
            f'<div class="form-group"><label class="form-label">{label}</label>'
            f'<input type="color" name="cfg_{key}" value="{val}" '
            f'style="height:36px;width:60px;padding:0;border:none;background:none;cursor:pointer;"></div>'
        )
    if vtype == "integer":
        val = value if value is not None else ""
        return (
            f'<div class="form-group"><label class="form-label">{label}</label>'
            f'<input type="number" name="cfg_{key}" value="{val}" class="form-input"></div>'
        )
    if vtype == "textarea":
        val = value if value is not None else ""
        return (
            f'<div class="form-group"><label class="form-label">{label}</label>'
            f'<textarea name="cfg_{key}" class="form-textarea" rows="3">{val}</textarea></div>'
        )
    # string / json default
    val = value if value is not None else ""
    if isinstance(val, (dict, list)):
        val = json.dumps(val)
    return (
        f'<div class="form-group"><label class="form-label">{label}</label>'
        f'<input type="text" name="cfg_{key}" value="{val}" class="form-input"></div>'
    )


def save_schema_form(app_record):
    """
    Persist a submitted generated settings form. Reads `cfg_*` fields and
    writes typed config rows. Returns (ok, message).
    """
    from appmanager.app_config import set_config

    schema = settings_schema_for(app_record)
    if not schema:
        return False, "No settings schema declared for this app."
    for desc in schema:
        key = desc.get("key")
        if not key:
            continue
        vtype = desc.get("type", "json")
        field = f"cfg_{key}"
        if vtype == "boolean":
            value = request.form.get(field) == "1"
        elif vtype == "integer":
            raw = request.form.get(field, "").strip()
            value = int(raw) if raw else None
        elif vtype == "json":
            raw = request.form.get(field, "").strip()
            try:
                value = json.loads(raw) if raw else None
            except Exception:
                return False, f"Invalid JSON for '{key}'."
        else:
            value = request.form.get(field, "")
        set_config(
            app_record.id, key, value, value_type=vtype, is_secret=desc.get("is_secret", False)
        )
    return True, "Settings saved."


# ---------------------------------------------------------------------------
# Kill switch — render a panel safely, never take down /admin
# ---------------------------------------------------------------------------


def render_panel_safe(app_record, panel, **ctx):
    """
    Render a single admin panel's content. If the panel is a generated form,
    render it. If it's a blueprint panel, we can't easily call its endpoint
    here — instead we link to it. Any error renders an inline error card.
    """
    try:
        if not panel.get("endpoint"):
            from appmanager.app_config import get_configs

            form = render_settings_form(app_record, get_configs(app_record.id))
            if form is None:
                return '<div class="empty-state"><p>No settings schema or admin panel declared for this app.</p></div>'
            return form
        # Blueprint panel — render a link card (content lives at its own route)
        url = f"/admin/apps/{app_record.slug}/{panel['panel_id']}"
        return (
            f'<div class="panel-link-card">'
            f'<p class="text-muted">This app provides a custom admin panel.</p>'
            f'<a href="{url}" class="btn btn-primary btn-sm">Open {panel["label"]}</a>'
            f"</div>"
        )
    except Exception as e:
        tb = traceback.format_exc()
        return (
            f'<div class="alert alert-danger">'
            f"<strong>Panel error:</strong> {e}"
            f'<details class="mt-1"><summary class="mono" style="font-size:0.75rem;cursor:pointer;">traceback</summary>'
            f'<pre class="mono" style="font-size:0.7rem;overflow:auto;max-height:200px;">{tb}</pre></details>'
            f"</div>"
        )
