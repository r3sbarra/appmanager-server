"""
User Flairs Extension — reference example of the AppManager extension contract.

This extension demonstrates the full AppManager extension surface:

  1. **Entry point** (`extension:extension`) — a Flask app the host loads.
  2. **UI slots** — registers a `user_badge` slot so the host renders a flair
     badge next to any member's name.
  3. **Admin blueprint** (`flairs_admin:bp`) — declared in `manifest.json`
     under `admin_sections`; the host mounts it at
     `/admin/apps/extension-flairs/assign` with admin auth enforced at mount
     time (the extension never self-guards).
  4. **Settings schema** — declared in `manifest.json` under `settings_schema`;
     the host renders a generated typed form and persists values to the
     `app_configs` table. The extension reads them via `client.get_setting()`.
  5. **Extension data** — per-entity JSON stored via `client.get_data()` /
     `client.set_data()` (backed by the `app_extension_data` table).

The host delegates flair lookups to this module through `appmanager.extensions`
(see `get_user_flair` / `set_user_flair` / `render_user_flair_badge` there),
which call back into the functions defined here. If this extension is not
installed, the host degrades gracefully to "no flair".
"""

from flask import Flask, jsonify

from appmanager.sdk import AppManagerClient

# ---------------------------------------------------------------------------
# Extension identity
# ---------------------------------------------------------------------------

SLUG = "extension-flairs"

extension = Flask(__name__)
extension.secret_key = "extension-flairs-secret-key-32-bytes"

client = AppManagerClient(SLUG)

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

DEFAULT_PRESET_FLAIRS = [
    {"title": "🚀 Lead Developer", "color": "#38bdf8"},
    {"title": "⭐ VIP Member", "color": "#facc15"},
    {"title": "🛡️ Moderator", "color": "#4ade80"},
    {"title": "🔥 Core Contributor", "color": "#f87171"},
    {"title": "💎 Sponsor", "color": "#c084fc"},
]


def preset_flairs():
    """Return the preset flair list (could be overridden by settings later)."""
    return DEFAULT_PRESET_FLAIRS


# ---------------------------------------------------------------------------
# Data access (per-user flair stored as extension data)
# ---------------------------------------------------------------------------


def get_user_flair(user_id):
    """Return {'title', 'color'} for a user, or None if unset."""
    data = client.get_data("user", user_id)
    if data and isinstance(data, dict):
        return {
            "title": data.get("title", ""),
            "color": data.get("color", client.get_setting("default_color", "#F5A524")),
        }
    return None


def set_user_flair(user_id, title, color=None):
    """Set (or clear, when title is empty) a user's flair."""
    if not title:
        client.set_data("user", user_id, None)
        return
    color = color or client.get_setting("default_color", "#F5A524")
    client.set_data(
        "user",
        user_id,
        {
            "title": title.strip(),
            "color": color.strip() if color else "#F5A524",
        },
    )


# ---------------------------------------------------------------------------
# Badge rendering (registered as the `user_badge` UI slot)
# ---------------------------------------------------------------------------


def render_user_flair_badge(user_id):
    """Return styled HTML for a user's flair badge (empty string if none)."""
    from markupsafe import Markup

    flair = get_user_flair(user_id)
    if not flair or not flair["title"]:
        return Markup("")
    title = flair["title"]
    color = flair["color"]
    html = (
        f'<span style="background: {color}22; color: {color}; '
        f"border: 1px solid {color}44; padding: 2px 8px; border-radius: 9999px; "
        f"font-size: 0.75rem; font-weight: 600; margin-left: 6px; "
        f'display: inline-block;">{title}</span>'
    )
    return Markup(html)


# ---------------------------------------------------------------------------
# Slot registration — runs when the host loads this extension
# ---------------------------------------------------------------------------


def register_slots():
    """Register UI slots with the host. Called by the host at startup."""
    client.register_slot("user_badge", render_user_flair_badge, priority=10)


# ---------------------------------------------------------------------------
# Routes (the extension's own public surface, if any)
# ---------------------------------------------------------------------------


@extension.route("/health")
def health():
    return jsonify(
        {
            "status": "healthy",
            "app_type": "extension",
            "extension_slug": SLUG,
            "target_app": "appmanager",
        }
    )
