import os

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from appmanager.admin.app_installer import (
    cancel_staged_app,
    finalize_staged_installation,
    finalize_zip_replacement,
    install_from_git,
    install_from_zip,
    sanitize_slug,
    stage_git_repo,
    stage_zip_file,
    stage_zip_replacement,
    uninstall_app,
    update_app_from_git,
)
from appmanager.admin.members import (
    DEFAULT_PER_PAGE,
    PER_PAGE_CHOICES,
    app_access_counts,
    list_members,
    member_permissions,
    total_app_count,
)
from appmanager.auth.utils import admin_required, get_current_user
from appmanager.database import db
from appmanager.dependency_manager import analyze_dependencies
from appmanager.health import check_all_apps_health
from appmanager.models import AppHealthLog, InstalledApp, Role, User, UserAppPermission

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@admin_required
def dashboard():
    """
    Consolidated admin dashboard. Renders all admin sections (apps, users,
    roles, flairs, permissions) as tabs on a single page.
    """
    from appmanager.extensions import DEFAULT_PRESET_FLAIRS, get_user_flair

    user = get_current_user()
    apps = InstalledApp.query.order_by(InstalledApp.installed_at.desc()).all()
    users = User.query.order_by(User.created_at.desc()).all()
    roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()

    # Get latest health log for each app
    health_map = {}
    health_history = {}
    for a in apps:
        latest = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .first()
        )
        health_map[a.id] = latest
        # Recent health history (for braille sparkline) — last 12 checks, oldest first
        history = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .limit(12)
            .all()
        )
        health_history[a.id] = list(reversed(history))

    # Role member counts
    role_counts = {r.slug: User.query.filter_by(role=r.slug).count() for r in roles}

    # Permission matrix map
    perms_map = {(p.user_id, p.app_id): p.can_access for p in UserAppPermission.query.all()}

    # Flair assignments
    user_flairs = {u.id: get_user_flair(u.id) for u in users}

    # Member list pagination context (for the members tab fragment)
    from appmanager.admin.members import (
        DEFAULT_PER_PAGE,
        PER_PAGE_CHOICES,
        app_access_counts,
        list_members,
        total_app_count,
    )

    members_pagination = list_members(page=1, per_page=DEFAULT_PER_PAGE)
    members_access_counts = app_access_counts([u.id for u in members_pagination.items])

    return render_template(
        "admin/dashboard.html",
        user=user,
        apps=apps,
        users=users,
        roles=roles,
        health_map=health_map,
        health_history=health_history,
        role_counts=role_counts,
        perms_map=perms_map,
        user_flairs=user_flairs,
        presets=DEFAULT_PRESET_FLAIRS,
        members_pagination=members_pagination,
        pagination=members_pagination,
        members_q="",
        q="",
        members_role="",
        role="",
        members_sort="last_login",
        sort="last_login",
        members_per_page=DEFAULT_PER_PAGE,
        per_page=DEFAULT_PER_PAGE,
        members_per_page_choices=PER_PAGE_CHOICES,
        per_page_choices=PER_PAGE_CHOICES,
        members_access_counts=members_access_counts,
        access_counts=members_access_counts,
        members_total_apps=total_app_count(),
        total_apps=total_app_count(),
    )


@admin_bp.route("/health/check-all", methods=["POST"])
@admin_required
def run_health_checks_route():
    results = check_all_apps_health()
    flash(f"Executed health checks for {len(results)} active applications.", "info")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/precheck-git", methods=["POST"])
@admin_required
def precheck_git():
    repo_url = (
        request.form.get("repo_url")
        or (request.json.get("repo_url") if request.is_json else "")
        or ""
    ).strip()
    name = (
        request.form.get("name") or (request.json.get("name") if request.is_json else "") or ""
    ).strip()
    slug = (
        request.form.get("slug") or (request.json.get("slug") if request.is_json else "") or ""
    ).strip() or None
    entry_point = (
        request.form.get("entry_point")
        or (request.json.get("entry_point") if request.is_json else "")
        or ""
    ).strip() or None

    if not repo_url or not name:
        return jsonify({"success": False, "error": "Repository URL and Name are required."}), 400

    try:
        staging_id, scan_report, manifest = stage_git_repo(
            repo_url=repo_url,
            name=name,
            slug=slug,
            entry_point=entry_point,
        )
        from appmanager.admin.app_installer import get_staged_session

        staged_info = get_staged_session(staging_id) or {}
        dep_report = staged_info.get("dependency_report")
        return jsonify(
            {
                "success": True,
                "staging_id": staging_id,
                "report": scan_report.to_dict(),
                "dependency_report": dep_report.to_dict() if dep_report else None,
                "venv_mode": current_app.config.get("APP_VENV_MODE", "singular"),
                "manifest": manifest,
                "name": manifest.get("name") or name,
                "slug": manifest.get("slug") or slug or sanitize_slug(name),
                "entry_point": manifest.get("entry_point") or entry_point or "app:app",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@admin_bp.route("/apps/precheck-zip", methods=["POST"])
@admin_required
def precheck_zip():
    if "zip_file" not in request.files:
        return jsonify({"success": False, "error": "No zip file uploaded."}), 400

    file = request.files["zip_file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No selected zip file."}), 400

    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip() or None
    entry_point = request.form.get("entry_point", "").strip() or None

    if not name:
        return jsonify({"success": False, "error": "App Name is required."}), 400

    try:
        staging_id, scan_report, manifest = stage_zip_file(
            zip_file_storage=file,
            name=name,
            slug=slug,
            entry_point=entry_point,
        )
        from appmanager.admin.app_installer import get_staged_session

        staged_info = get_staged_session(staging_id) or {}
        dep_report = staged_info.get("dependency_report")
        return jsonify(
            {
                "success": True,
                "staging_id": staging_id,
                "report": scan_report.to_dict(),
                "dependency_report": dep_report.to_dict() if dep_report else None,
                "venv_mode": current_app.config.get("APP_VENV_MODE", "singular"),
                "manifest": manifest,
                "name": manifest.get("name") or name,
                "slug": manifest.get("slug") or slug or sanitize_slug(name),
                "entry_point": manifest.get("entry_point") or entry_point or "app:app",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@admin_bp.route("/apps/install-confirm", methods=["POST"])
@admin_required
def install_confirm():
    staging_id = (
        request.form.get("staging_id")
        or (request.json.get("staging_id") if request.is_json else "")
        or ""
    ).strip()
    name = (
        request.form.get("name") or (request.json.get("name") if request.is_json else "") or ""
    ).strip() or None
    slug = (
        request.form.get("slug") or (request.json.get("slug") if request.is_json else "") or ""
    ).strip() or None
    entry_point = (
        request.form.get("entry_point")
        or (request.json.get("entry_point") if request.is_json else "")
        or ""
    ).strip() or None

    if not staging_id:
        if request.is_json:
            return jsonify({"success": False, "error": "Missing staging ID."}), 400
        flash("Missing staging ID.", "danger")
        return redirect(url_for("admin.dashboard"))

    try:
        app_record = finalize_staged_installation(
            staging_id=staging_id,
            name=name,
            slug=slug,
            entry_point=entry_point,
        )
        if request.is_json:
            return jsonify(
                {
                    "success": True,
                    "app_id": app_record.id,
                    "name": app_record.name,
                    "slug": app_record.slug,
                    "message": f"Application '{app_record.name}' installed successfully!",
                }
            )
        flash(f"Application '{app_record.name}' installed successfully!", "success")
    except Exception as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Installation failed: {str(e)}", "danger")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/cancel-staged", methods=["POST"])
@admin_required
def cancel_staged():
    staging_id = (
        request.form.get("staging_id")
        or (request.json.get("staging_id") if request.is_json else "")
        or ""
    ).strip()
    if staging_id:
        cancel_staged_app(staging_id)
    return jsonify({"success": True})


@admin_bp.route("/apps/install-git", methods=["POST"])
@admin_required
def install_git():
    repo_url = request.form.get("repo_url", "").strip()
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip()
    entry_point = request.form.get("entry_point", "").strip() or None

    if not repo_url or not name:
        flash("Repository URL and Name are required.", "danger")
        return redirect(url_for("admin.dashboard"))

    try:
        app_record = install_from_git(
            repo_url=repo_url, name=name, slug=slug, entry_point=entry_point
        )
        flash(f"Application '{app_record.name}' installed successfully from Git!", "success")
    except Exception as e:
        flash(f"Failed to install application from Git: {str(e)}", "danger")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/install-zip", methods=["POST"])
@admin_required
def install_zip():
    if "zip_file" not in request.files:
        flash("No file part uploaded.", "danger")
        return redirect(url_for("admin.dashboard"))

    file = request.files["zip_file"]
    if file.filename == "":
        flash("No selected file.", "danger")
        return redirect(url_for("admin.dashboard"))

    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip()
    entry_point = request.form.get("entry_point", "").strip() or None

    if not name:
        flash("App Name is required.", "danger")
        return redirect(url_for("admin.dashboard"))

    try:
        app_record = install_from_zip(
            zip_file_storage=file, name=name, slug=slug, entry_point=entry_point
        )
        flash(f"Application '{app_record.name}' uploaded and installed successfully!", "success")
    except Exception as e:
        flash(f"Failed to install application from ZIP: {str(e)}", "danger")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/update-git", methods=["POST"])
@admin_required
def update_git_app(app_id):
    success, msg, details = update_app_from_git(app_id)
    if request.is_json:
        return jsonify({"success": success, "message": msg, "details": details}), (
            200 if success else 400
        )

    if success:
        flash(msg, "success")
    else:
        flash(f"Update failed: {msg}", "danger")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/precheck-replace-zip", methods=["POST"])
@admin_required
def precheck_replace_zip(app_id):
    if "zip_file" not in request.files:
        return jsonify({"success": False, "error": "No zip file uploaded."}), 400

    file = request.files["zip_file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No selected zip file."}), 400

    try:
        staging_id, scan_report, manifest, dep_report = stage_zip_replacement(
            app_id_or_slug=app_id, zip_file_storage=file
        )
        return jsonify(
            {
                "success": True,
                "staging_id": staging_id,
                "report": scan_report.to_dict(),
                "dependency_report": dep_report.to_dict() if dep_report else None,
                "venv_mode": current_app.config.get("APP_VENV_MODE", "singular"),
                "manifest": manifest,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@admin_bp.route("/apps/confirm-replace-zip", methods=["POST"])
@admin_required
def confirm_replace_zip():
    staging_id = (
        request.form.get("staging_id")
        or (request.json.get("staging_id") if request.is_json else "")
        or ""
    ).strip()

    if not staging_id:
        if request.is_json:
            return jsonify({"success": False, "error": "Missing staging ID."}), 400
        flash("Missing staging ID.", "danger")
        return redirect(url_for("admin.dashboard"))

    try:
        app_record = finalize_zip_replacement(staging_id)
        if request.is_json:
            return jsonify(
                {
                    "success": True,
                    "app_id": app_record.id,
                    "name": app_record.name,
                    "slug": app_record.slug,
                    "message": f"Application '{app_record.name}' updated successfully with replacement package!",
                }
            )
        flash(
            f"Application '{app_record.name}' updated successfully with replacement package!",
            "success",
        )
    except Exception as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Replacement failed: {str(e)}", "danger")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/dependencies", methods=["GET"])
@admin_required
def app_dependencies(app_id):
    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        return jsonify({"success": False, "error": "App not found."}), 404

    app_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], app_record.slug)
    from appmanager.admin.app_installer import parse_manifest

    manifest = parse_manifest(app_dir) or {}
    venv_mode = current_app.config.get("APP_VENV_MODE", "singular")
    dep_report = analyze_dependencies(app_dir, manifest=manifest, venv_mode=venv_mode)

    return jsonify(
        {
            "success": True,
            "app_id": app_record.id,
            "name": app_record.name,
            "slug": app_record.slug,
            "dependencies": dep_report.to_dict(),
        }
    )


@admin_bp.route("/apps/<int:app_id>/delete", methods=["POST"])
@admin_required
def delete_app(app_id):
    success, msg = uninstall_app(app_id)
    if success:
        flash(msg, "success")
    else:
        flash(msg, "danger")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/toggle-auth", methods=["POST"])
@admin_required
def toggle_auth(app_id):
    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    app_record.requires_auth = not app_record.requires_auth
    db.session.commit()
    status_str = "Login Protected" if app_record.requires_auth else "Public / Anonymous"
    flash(f"Updated authentication setting for '{app_record.name}' to {status_str}.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/set-default", methods=["POST"])
@admin_required
def set_default(app_id):
    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Unset default on all other apps
    all_apps = InstalledApp.query.all()
    for a in all_apps:
        if a.id == app_record.id:
            a.is_default = not a.is_default
        else:
            a.is_default = False

    db.session.commit()
    msg = (
        f"'{app_record.name}' is now set as the Default Landing App!"
        if app_record.is_default
        else "Cleared default app setting."
    )
    flash(msg, "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_active(app_id):
    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    app_record.is_active = not app_record.is_active
    db.session.commit()

    # Clear cache if deactivated
    if (
        not app_record.is_active
        and hasattr(current_app, "extensions")
        and "appmanager" in current_app.extensions
    ):
        current_app.extensions["appmanager"].clear_cache(slug=app_record.slug)

    status_str = "Active / Visible" if app_record.is_active else "Inactive / Hidden"
    flash(f"Updated status for '{app_record.name}' to {status_str}.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<int:app_id>/settings", methods=["POST"])
@admin_required
def update_app_settings(app_id):
    import json

    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    raw_json = request.form.get("settings_json", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if not isinstance(parsed, dict):
                raise ValueError("Settings must be a JSON object.")
            app_record.set_settings(parsed)
            db.session.commit()
            flash(f"Updated configuration settings for '{app_record.name}'.", "success")
        except Exception as e:
            flash(f"Invalid JSON format for settings: {str(e)}", "danger")
    else:
        app_record.settings_json = None
        db.session.commit()
        flash(f"Cleared settings for '{app_record.name}'.", "info")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/apps/<slug>/settings", methods=["POST"])
@admin_required
def save_app_schema_settings(slug):
    """Save a generated settings form (settings_schema) for an app."""
    from appmanager.admin.registry import save_schema_form

    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))
    ok, msg = save_schema_form(app_record)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("admin.app_detail", slug=slug))


@admin_bp.route("/apps/<slug>")
@admin_required
def app_detail(slug):
    """
    App detail page: shows the app's own admin panels (generated settings form
    and/or links to custom blueprint panels).
    """
    from appmanager.admin.registry import (
        manifest_for,
        panels_for,
        render_panel_safe,
        settings_schema_for,
    )
    from appmanager.app_config import get_configs

    user = get_current_user()
    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        flash("App not found.", "danger")
        return redirect(url_for("admin.dashboard"))

    panels = panels_for(app_record)
    schema = settings_schema_for(app_record)
    configs = get_configs(app_record.id)
    manifest = manifest_for(app_record)

    # Render each panel's content safely (kill switch)
    rendered = []
    for panel in panels:
        rendered.append(
            {
                "panel": panel,
                "html": render_panel_safe(app_record, panel, configs=configs),
            }
        )

    app_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], app_record.slug)
    venv_mode = current_app.config.get("APP_VENV_MODE", "singular")
    dep_report = analyze_dependencies(app_dir, manifest=manifest, venv_mode=venv_mode)

    return render_template(
        "admin/app_detail.html",
        user=user,
        app=app_record,
        panels=panels,
        rendered=rendered,
        schema=schema,
        configs=configs,
        manifest=manifest,
        dep_report=dep_report,
    )


@admin_bp.route("/permissions", methods=["GET", "POST"])
@admin_required
def permissions():
    if request.method == "POST":
        # Processing matrix update
        users = User.query.all()
        apps = InstalledApp.query.all()

        for u in users:
            for a in apps:
                form_key = f"perm_{u.id}_{a.id}"
                has_access = request.form.get(form_key) == "1"
                perm = UserAppPermission.query.filter_by(user_id=u.id, app_id=a.id).first()
                if not perm:
                    perm = UserAppPermission(user_id=u.id, app_id=a.id, can_access=has_access)
                    db.session.add(perm)
                else:
                    perm.can_access = has_access

        db.session.commit()
        flash("App permissions updated successfully!", "success")
        return redirect(url_for("admin.dashboard"))

    users = User.query.order_by(User.email).all()
    apps = InstalledApp.query.order_by(InstalledApp.name).all()

    # Map current permissions for fast lookup
    perms_map = {}
    all_perms = UserAppPermission.query.all()
    for p in all_perms:
        perms_map[(p.user_id, p.app_id)] = p.can_access

    return render_template("admin/permissions.html", users=users, apps=apps, perms_map=perms_map)


@admin_bp.route("/users")
@admin_required
def users_list():
    user = get_current_user()
    users = User.query.order_by(User.created_at.desc()).all()
    apps = InstalledApp.query.order_by(InstalledApp.name).all()
    roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
    perms_map = {(p.user_id, p.app_id): p.can_access for p in UserAppPermission.query.all()}
    return render_template(
        "admin/users.html", user=user, users=users, apps=apps, roles=roles, perms_map=perms_map
    )


@admin_bp.route("/users/<int:user_id>/edit", methods=["POST"])
@admin_required
def edit_user(user_id):
    u = db.session.get(User, user_id)
    if not u:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users_list"))

    u.name = request.form.get("name", "").strip() or u.name
    u.role = request.form.get("role", "user").strip().lower()
    u.is_active = request.form.get("is_active") == "1"

    # Update app permissions for this user
    apps = InstalledApp.query.all()
    for app in apps:
        form_key = f"perm_{app.id}"
        has_access = request.form.get(form_key) == "1"
        perm = UserAppPermission.query.filter_by(user_id=u.id, app_id=app.id).first()
        if not perm:
            perm = UserAppPermission(user_id=u.id, app_id=app.id, can_access=has_access)
            db.session.add(perm)
        else:
            perm.can_access = has_access

    # Update flair for this user if provided
    flair_title = request.form.get("flair_title")
    flair_color = request.form.get("flair_color", "#a855f7")
    if flair_title is not None:
        from appmanager.extensions import set_user_flair

        set_user_flair(u.id, flair_title, flair_color)

    db.session.commit()
    flash(f"Successfully updated user details, permissions, and flair for '{u.email}'.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/roles", methods=["GET"])
@admin_required
def manage_roles():
    user = get_current_user()
    roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()

    # Compute member count per role
    role_counts = {}
    for r in roles:
        role_counts[r.slug] = User.query.filter_by(role=r.slug).count()

    return render_template("admin/roles.html", user=user, roles=roles, role_counts=role_counts)


@admin_bp.route("/roles/create", methods=["POST"])
@admin_required
def create_role():
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip() or sanitize_slug(name)
    description = request.form.get("description", "").strip()

    if not name:
        flash("Role name is required.", "danger")
        return redirect(url_for("admin.dashboard"))

    clean_slug = sanitize_slug(slug)
    existing = Role.query.filter((Role.slug == clean_slug) | (Role.name == name)).first()
    if existing:
        flash(f"A role with name '{name}' or slug '{clean_slug}' already exists.", "warning")
        return redirect(url_for("admin.dashboard"))

    new_role = Role(name=name, slug=clean_slug, description=description, is_system=False)
    db.session.add(new_role)
    db.session.commit()
    flash(f"Role '{name}' (slug: {clean_slug}) created successfully!", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/roles/<int:role_id>/edit", methods=["POST"])
@admin_required
def edit_role(role_id):
    role_record = db.session.get(Role, role_id)
    if not role_record:
        flash("Role not found.", "danger")
        return redirect(url_for("admin.manage_roles"))

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if name and not role_record.is_system:
        role_record.name = name
    role_record.description = description
    db.session.commit()
    flash(f"Updated role '{role_record.name}'.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@admin_required
def delete_role(role_id):
    role_record = db.session.get(Role, role_id)
    if not role_record:
        flash("Role not found.", "danger")
        return redirect(url_for("admin.manage_roles"))

    if role_record.is_system:
        flash(f"Cannot delete system role '{role_record.name}'.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Reassign any users with this role to 'user'
    affected_users = User.query.filter_by(role=role_record.slug).all()
    for u in affected_users:
        u.role = "user"

    db.session.delete(role_record)
    db.session.commit()
    flash(
        f"Deleted role '{role_record.name}'. Reassigned {len(affected_users)} member(s) to 'user'.",
        "success",
    )
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/flairs", methods=["GET", "POST"])
@admin_required
def manage_flairs():
    from appmanager.extensions import DEFAULT_PRESET_FLAIRS, get_user_flair, set_user_flair

    if request.method == "POST":
        user_id = request.form.get("user_id", type=int)
        flair_title = request.form.get("flair_title", "").strip()
        flair_color = request.form.get("flair_color", "#a855f7").strip()
        action = request.form.get("action", "save")

        if not user_id:
            flash("Invalid user selection.", "danger")
            return redirect(url_for("admin.dashboard"))

        if action == "clear":
            set_user_flair(user_id, "", "#a855f7")
            flash(f"Cleared flair for user #{user_id}.", "info")
        else:
            set_user_flair(user_id, flair_title, flair_color)
            flash(f"Updated flair for user #{user_id} successfully!", "success")

        return redirect(url_for("admin.dashboard"))

    users = User.query.order_by(User.created_at.desc()).all()
    user_flairs = {u.id: get_user_flair(u.id) for u in users}
    return render_template(
        "admin/flairs.html", users=users, user_flairs=user_flairs, presets=DEFAULT_PRESET_FLAIRS
    )


# ---------------------------------------------------------------------------
# Member management (scalable) — paginated list + detail drawer + bulk ops
# ---------------------------------------------------------------------------


@admin_bp.route("/members")
@admin_required
def members_list():
    """
    Paginated, searchable member list. Renders a fragment when requested via
    htmx (HX-Request header) so the table can refresh in place; otherwise
    renders the full dashboard with the members tab active.
    """
    from appmanager.extensions import get_user_flair

    user = get_current_user()
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip() or None
    sort = request.args.get("sort", "last_login")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", DEFAULT_PER_PAGE, type=int)

    pagination = list_members(q=q, role=role, page=page, per_page=per_page, sort=sort)
    users = pagination.items
    user_ids = [u.id for u in users]
    access_counts = app_access_counts(user_ids)
    user_flairs = {u.id: get_user_flair(u.id) for u in users}
    roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
    total_apps = total_app_count()

    ctx = dict(
        user=user,
        users=users,
        roles=roles,
        pagination=pagination,
        q=q,
        role=role,
        sort=sort,
        per_page=per_page,
        per_page_choices=PER_PAGE_CHOICES,
        access_counts=access_counts,
        user_flairs=user_flairs,
        total_apps=total_apps,
    )

    if request.headers.get("HX-Request"):
        return render_template("admin/_members_table.html", **ctx)

    # Full page render: reuse the dashboard shell but activate the members tab
    apps = InstalledApp.query.order_by(InstalledApp.installed_at.desc()).all()
    health_map = {}
    health_history = {}
    for a in apps:
        latest = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .first()
        )
        health_map[a.id] = latest
        history = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .limit(12)
            .all()
        )
        health_history[a.id] = list(reversed(history))
    role_counts = {r.slug: User.query.filter_by(role=r.slug).count() for r in roles}
    perms_map = {(p.user_id, p.app_id): p.can_access for p in UserAppPermission.query.all()}
    from appmanager.extensions import DEFAULT_PRESET_FLAIRS

    return render_template(
        "admin/dashboard.html",
        user=user,
        apps=apps,
        users=users,
        roles=roles,
        health_map=health_map,
        health_history=health_history,
        role_counts=role_counts,
        perms_map=perms_map,
        user_flairs=user_flairs,
        presets=DEFAULT_PRESET_FLAIRS,
        active_tab="members",
        members_pagination=pagination,
        pagination=pagination,
        members_q=q,
        q=q,
        members_role=role,
        role=role,
        members_sort=sort,
        sort=sort,
        members_per_page=per_page,
        per_page=per_page,
        members_per_page_choices=PER_PAGE_CHOICES,
        per_page_choices=PER_PAGE_CHOICES,
        members_access_counts=access_counts,
        access_counts=access_counts,
        members_total_apps=total_apps,
        total_apps=total_apps,
    )


@admin_bp.route("/members/<int:user_id>")
@admin_required
def member_detail(user_id):
    """Renders the member edit drawer content (fragment)."""
    from appmanager.extensions import DEFAULT_PRESET_FLAIRS, get_user_flair

    u = db.session.get(User, user_id)
    if not u:
        return ("", 404)
    roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
    perms = member_permissions(user_id)
    flair = get_user_flair(user_id)
    return render_template(
        "admin/_member_drawer.html",
        u=u,
        roles=roles,
        perms=perms,
        flair=flair,
        presets=DEFAULT_PRESET_FLAIRS,
    )


@admin_bp.route("/members/<int:user_id>/edit", methods=["POST"])
@admin_required
def edit_member(user_id):
    """Save member identity/role/status/permissions/flair. Returns to members list."""
    u = db.session.get(User, user_id)
    if not u:
        flash("User not found.", "danger")
        return redirect(url_for("admin.members_list"))

    u.name = request.form.get("name", "").strip() or u.name
    u.role = request.form.get("role", "user").strip().lower()
    u.is_active = request.form.get("is_active") == "1"

    # Update app permissions for this user (skip if admin — inherited)
    if not u.is_admin():
        apps = InstalledApp.query.all()
        for app in apps:
            form_key = f"perm_{app.id}"
            has_access = request.form.get(form_key) == "1"
            perm = UserAppPermission.query.filter_by(user_id=u.id, app_id=app.id).first()
            if not perm:
                perm = UserAppPermission(user_id=u.id, app_id=app.id, can_access=has_access)
                db.session.add(perm)
            else:
                perm.can_access = has_access

    # Update flair if provided
    flair_title = request.form.get("flair_title")
    flair_color = request.form.get("flair_color", "#F5A524")
    if flair_title is not None:
        from appmanager.extensions import set_user_flair

        set_user_flair(u.id, flair_title, flair_color)

    db.session.commit()
    flash(f"Updated member '{u.email}'.", "success")
    return redirect(url_for("admin.members_list"))


@admin_bp.route("/members/bulk", methods=["POST"])
@admin_required
def members_bulk():
    """Bulk actions on selected members: set role, deactivate, activate, delete."""
    action = request.form.get("action", "")
    ids = [int(x) for x in request.form.getlist("user_ids") if x.isdigit()]
    if not ids:
        flash("No members selected.", "warning")
        return redirect(url_for("admin.members_list"))

    users = User.query.filter(User.id.in_(ids)).all()
    if action == "set_role":
        role = request.form.get("role", "").strip().lower()
        if role:
            for u in users:
                u.role = role
            db.session.commit()
            flash(f"Set role '{role}' on {len(users)} member(s).", "success")
    elif action == "deactivate":
        for u in users:
            u.is_active = False
        db.session.commit()
        flash(f"Deactivated {len(users)} member(s).", "success")
    elif action == "activate":
        for u in users:
            u.is_active = True
        db.session.commit()
        flash(f"Activated {len(users)} member(s).", "success")
    elif action == "delete":
        for u in users:
            db.session.delete(u)
        db.session.commit()
        flash(f"Deleted {len(users)} member(s).", "success")
    else:
        flash("Unknown bulk action.", "warning")

    return redirect(url_for("admin.members_list"))
