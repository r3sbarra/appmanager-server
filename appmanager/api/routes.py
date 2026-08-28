from datetime import datetime, timezone
from functools import wraps

from flask import current_app, jsonify, request

from appmanager.admin.app_installer import install_from_git, install_from_zip, uninstall_app
from appmanager.api import api_bp
from appmanager.auth.utils import decode_jwt
from appmanager.database import db
from appmanager.health import check_app_health
from appmanager.models import AppHealthLog, AppTelemetryLog, InstalledApp, Role, User


def api_auth_required(f):
    """
    Decorator requiring valid API key (X-API-Key) or Bearer Admin JWT for API endpoints.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        configured_api_key = current_app.config.get("APPMANAGER_API_KEY")

        # 1. Check API Key header if configured
        api_key = request.headers.get("X-API-Key")
        if configured_api_key and api_key == configured_api_key:
            return f(*args, **kwargs)

        # 2. Check Bearer Token (JWT)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            # Check if token matches API key
            if configured_api_key and token == configured_api_key:
                return f(*args, **kwargs)
            # Check if token is admin JWT
            payload = decode_jwt(token)
            if payload:
                user = db.session.get(User, payload.get("user_id"))
                if user and user.is_active and user.is_admin():
                    return f(*args, **kwargs)

        # If no API key is set in config and no auth header, in dev/testing check if admin logged in
        if not configured_api_key:
            # Check cookie or JWT
            token = request.cookies.get("appmanager_jwt")
            if token:
                payload = decode_jwt(token)
                if payload:
                    user = db.session.get(User, payload.get("user_id"))
                    if user and user.is_active and user.is_admin():
                        return f(*args, **kwargs)

        return jsonify(
            {
                "success": False,
                "error": "Unauthorized",
                "message": "Valid X-API-Key or Admin Bearer Token is required.",
            }
        ), 401

    return decorated_function


@api_bp.route("/health", methods=["GET"])
def system_health():
    """
    Returns overall AppManager host system health and database connectivity.
    """
    db_ok = True
    db_error = None
    try:
        from sqlalchemy import text

        db.session.execute(text("SELECT 1"))
    except Exception as e:
        import logging

        logging.getLogger("appmanager.api").exception(f"Database health check failed: {e}")
        db_ok = False
        if current_app.config.get("DEBUG", False):
            db_error = str(e)

    total_apps = 0
    active_apps = 0
    if db_ok:
        try:
            total_apps = InstalledApp.query.count()
            active_apps = InstalledApp.query.filter_by(is_active=True).count()
        except Exception:
            pass

    overall_status = "healthy" if db_ok else "unhealthy"

    resp_data = {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {"connected": db_ok},
        "apps": {"total": total_apps, "active": active_apps},
    }
    if db_error is not None:
        resp_data["database"]["error"] = db_error

    return jsonify(resp_data), 200 if db_ok else 503


@api_bp.route("/apps", methods=["GET"])
def list_apps():
    """
    Lists all installed sub-applications and their latest health status.
    """
    apps = InstalledApp.query.all()
    results = []
    for a in apps:
        app_dict = a.to_dict()
        latest_health = (
            AppHealthLog.query.filter_by(app_id=a.id)
            .order_by(AppHealthLog.checked_at.desc())
            .first()
        )
        app_dict["latest_health"] = {
            "status": latest_health.status if latest_health else "unknown",
            "response_time_ms": latest_health.response_time_ms if latest_health else None,
            "checked_at": latest_health.checked_at.isoformat()
            if latest_health and latest_health.checked_at
            else None,
        }
        results.append(app_dict)
    return jsonify({"success": True, "apps": results, "count": len(results)}), 200


@api_bp.route("/apps/<slug>", methods=["GET"])
def get_app(slug):
    """
    Retrieves details for a specific sub-app by slug.
    """
    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        return jsonify({"success": False, "error": f"App with slug '{slug}' not found."}), 404

    app_dict = app_record.to_dict()
    latest_health = (
        AppHealthLog.query.filter_by(app_id=app_record.id)
        .order_by(AppHealthLog.checked_at.desc())
        .first()
    )
    app_dict["latest_health"] = {
        "status": latest_health.status if latest_health else "unknown",
        "response_time_ms": latest_health.response_time_ms if latest_health else None,
        "checked_at": latest_health.checked_at.isoformat()
        if latest_health and latest_health.checked_at
        else None,
    }
    return jsonify({"success": True, "app": app_dict}), 200


@api_bp.route("/apps/install", methods=["POST"])
@api_auth_required
def install_app():
    """
    Installs a sub-app via Git repository URL (JSON payload) or ZIP upload (multipart).
    """
    if request.is_json:
        data = request.get_json() or {}
        repo_url = data.get("repo_url", "").strip()
        name = data.get("name", "").strip()
        slug = data.get("slug", "").strip() or None
        description = data.get("description", "").strip() or None
        entry_point = data.get("entry_point", "").strip() or None

        if not repo_url or not name:
            return jsonify(
                {"success": False, "error": "Missing required fields: repo_url, name"}
            ), 400

        try:
            installed = install_from_git(
                repo_url, name, slug=slug, description=description, entry_point=entry_point
            )
            return jsonify(
                {
                    "success": True,
                    "message": f"App '{installed.name}' installed successfully.",
                    "app": installed.to_dict(),
                }
            ), 201
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    elif "zip_file" in request.files:
        zip_file = request.files["zip_file"]
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip() or None
        description = request.form.get("description", "").strip() or None
        entry_point = request.form.get("entry_point", "").strip() or None

        if not name:
            return jsonify({"success": False, "error": "Missing required field 'name'"}), 400

        try:
            installed = install_from_zip(
                zip_file, name, slug=slug, description=description, entry_point=entry_point
            )
            return jsonify(
                {
                    "success": True,
                    "message": f"App '{installed.name}' installed from ZIP.",
                    "app": installed.to_dict(),
                }
            ), 201
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    return jsonify(
        {
            "success": False,
            "error": "Invalid request format. Send JSON with repo_url or multipart with zip_file.",
        }
    ), 400


@api_bp.route("/apps/<slug>", methods=["DELETE"])
@api_auth_required
def delete_app_by_slug(slug):
    """
    Uninstalls an application by slug.
    """
    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        return jsonify({"success": False, "error": f"App with slug '{slug}' not found."}), 404

    success, msg = uninstall_app(app_record.id)
    if success:
        return jsonify({"success": True, "message": msg}), 200
    return jsonify({"success": False, "error": msg}), 500


@api_bp.route("/apps/<slug>/health-check", methods=["POST"])
@api_auth_required
def trigger_health_check(slug):
    """
    Triggers an immediate health check for a sub-app and returns the result.
    """
    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        return jsonify({"success": False, "error": f"App with slug '{slug}' not found."}), 404

    log = check_app_health(app_record)
    return jsonify(
        {
            "success": True,
            "app_slug": slug,
            "status": log.status,
            "response_time_ms": log.response_time_ms,
            "details": log.details,
            "checked_at": log.checked_at.isoformat() if log.checked_at else None,
        }
    ), 200


@api_bp.route("/apps/<slug>/reload", methods=["POST"])
@api_auth_required
def reload_app(slug):
    """
    Invalidates the in-memory WSGI cache for a sub-app to trigger live reload.
    """
    app_record = InstalledApp.query.filter_by(slug=slug).first()
    if not app_record:
        return jsonify({"success": False, "error": f"App with slug '{slug}' not found."}), 404

    if hasattr(current_app, "extensions") and "appmanager" in current_app.extensions:
        current_app.extensions["appmanager"].clear_cache(slug=slug)

    return jsonify({"success": True, "message": f"Cache cleared for sub-app '{slug}'."}), 200


@api_bp.route("/metrics", methods=["GET"])
def get_metrics():
    """
    Returns telemetry metrics summary.
    """
    recent_logs = AppTelemetryLog.query.order_by(AppTelemetryLog.created_at.desc()).limit(100).all()
    metrics_summary = {}
    for log in recent_logs:
        if log.app_slug not in metrics_summary:
            metrics_summary[log.app_slug] = {"total_events": 0, "latest_event": None}
        metrics_summary[log.app_slug]["total_events"] += 1
        if not metrics_summary[log.app_slug]["latest_event"]:
            metrics_summary[log.app_slug]["latest_event"] = {
                "event_type": log.event_type,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }

    return jsonify(
        {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics_summary,
        }
    ), 200


@api_bp.route("/roles", methods=["GET"])
@api_auth_required
def list_roles_api():
    """
    Returns list of all configured roles and member counts.
    """
    roles = Role.query.order_by(Role.name).all()
    data = []
    for r in roles:
        r_dict = r.to_dict()
        r_dict["member_count"] = User.query.filter_by(role=r.slug).count()
        data.append(r_dict)
    return jsonify({"success": True, "count": len(data), "roles": data})


@api_bp.route("/roles", methods=["POST"])
@api_auth_required
def create_role_api():
    """
    Creates a new custom role.
    """
    from appmanager.admin.app_installer import sanitize_slug

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip() or sanitize_slug(name)
    description = data.get("description", "").strip()

    if not name:
        return jsonify({"error": "Role name is required", "success": False}), 400

    clean_slug = sanitize_slug(slug)
    existing = Role.query.filter((Role.slug == clean_slug) | (Role.name == name)).first()
    if existing:
        return jsonify(
            {
                "error": f"Role with slug '{clean_slug}' or name '{name}' already exists",
                "success": False,
            }
        ), 409

    role = Role(name=name, slug=clean_slug, description=description, is_system=False)
    db.session.add(role)
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "message": f"Role '{role.name}' created successfully",
            "role": role.to_dict(),
        }
    ), 201


@api_bp.route("/roles/<string:slug>", methods=["DELETE"])
@api_auth_required
def delete_role_api(slug: str):
    """
    Deletes a custom role.
    """
    role = Role.query.filter_by(slug=slug).first()
    if not role:
        return jsonify({"error": "Role not found", "success": False}), 404

    if role.is_system:
        return jsonify(
            {"error": f"Cannot delete core system role '{role.name}'", "success": False}
        ), 400

    # Reassign affected users
    affected = User.query.filter_by(role=role.slug).all()
    for u in affected:
        u.role = "user"

    db.session.delete(role)
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "message": f"Role '{role.name}' deleted successfully",
            "reassigned_members": len(affected),
        }
    )
