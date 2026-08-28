"""
Flairs admin blueprint — a custom admin panel for the User Flairs extension.

This blueprint is declared in `manifest.json` under `admin_sections`:

    "admin_sections": [
      {"id": "assign", "label": "Assign Flairs", "icon": "tag",
       "blueprint": "flairs_admin:bp", "order": 10}
    ]

The host mounts it at `/admin/apps/extension-flairs/assign` and enforces admin
auth at mount time via a `before_request` — this extension does NOT (and must
not) self-guard. The host's kill switch also means a render error here shows
an inline error card instead of taking down /admin.

The panel reuses the host's design system (app.css) so it looks native.
"""

from extension import get_user_flair, preset_flairs, set_user_flair
from flask import Blueprint, flash, redirect, render_template, request, url_for

from appmanager.models import User

bp = Blueprint("flairs_admin", __name__)


@bp.route("/")
def assign():
    """List all members with their current flair and an assignment form."""
    users = User.query.order_by(User.email).all()
    user_flairs = {u.id: get_user_flair(u.id) for u in users}
    return render_template(
        "flairs_admin/assign.html",
        users=users,
        user_flairs=user_flairs,
        presets=preset_flairs(),
    )


@bp.route("/set", methods=["POST"])
def set_flair():
    """Assign or clear a flair for a member."""
    user_id = request.form.get("user_id", type=int)
    action = request.form.get("action", "save")
    title = request.form.get("flair_title", "").strip()
    color = request.form.get("flair_color", "").strip()

    if not user_id:
        flash("No member selected.", "danger")
        return redirect(url_for("admin.flairs_admin.assign"))

    if action == "clear":
        set_user_flair(user_id, "")
        flash("Flair cleared.", "info")
    else:
        if not title:
            flash("Flair title is required.", "danger")
        else:
            set_user_flair(user_id, title, color or None)
            flash("Flair updated.", "success")
    return redirect(url_for("admin.flairs_admin.assign"))
