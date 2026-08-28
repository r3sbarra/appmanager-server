import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from appmanager.database import db
from appmanager.models import AppTelemetryLog
from appmanager.signals import telemetry_received


def report_event(app_slug: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> bool:
    """
    Sub-apps can call report_event to log events directly to AppManager host telemetry.
    Zero-network overhead in-process communication.

    Rate-limited per app to prevent a single app from flooding the host.
    """
    from appmanager.ratelimit import allow

    if not allow(app_slug, "report_event"):
        return False

    try:
        payload = json.dumps(data) if data is not None else None
        log_entry = AppTelemetryLog(
            app_slug=app_slug,
            event_type=event_type,
            payload_json=payload,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(log_entry)
        db.session.commit()

        try:
            telemetry_received.send(None, app_slug=app_slug, event_type=event_type, data=data)
        except Exception:
            pass

        return True
    except Exception as e:
        db.session.rollback()
        print(f"[BRIDGE ERROR] Failed to record event for '{app_slug}': {e}")
        return False


def report_metric(
    app_slug: str, metric_name: str, value: Union[int, float], unit: Optional[str] = None
) -> bool:
    """
    Sub-apps can call report_metric to log numeric metrics.
    """
    data = {"value": value}
    if unit:
        data["unit"] = unit
    return report_event(app_slug, event_type=f"metric:{metric_name}", data=data)


def get_current_user_from_headers(headers: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """
    Helper for sub-apps to extract the authenticated user identity forwarded by AppManager.
    If headers is None, attempts to read from flask.request.headers or os.environ.
    """
    if headers is None:
        try:
            from flask import request

            headers = request.headers
        except Exception:
            headers = {}

    user_id = headers.get("X-AppManager-User-Id") or headers.get("HTTP_X_APPMANAGER_USER_ID")
    user_email = headers.get("X-AppManager-User-Email") or headers.get(
        "HTTP_X_APPMANAGER_USER_EMAIL"
    )
    user_role = headers.get("X-AppManager-User-Role") or headers.get("HTTP_X_APPMANAGER_USER_ROLE")

    if user_id or user_email:
        return {
            "id": int(user_id) if user_id and str(user_id).isdigit() else user_id,
            "email": user_email,
            "role": user_role or "user",
        }
    return None


def get_app_settings(app_slug: str) -> Dict[str, Any]:
    """
    Helper to retrieve configured settings dictionary for a sub-app.
    """
    from appmanager.models import InstalledApp

    try:
        app_rec = InstalledApp.query.filter_by(slug=app_slug).first()
        if app_rec:
            return app_rec.get_settings()
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Shared database access + read-only auth context (re-exported from db_access)
# ---------------------------------------------------------------------------

def get_db_engine(app_slug: str):
    """Return a SQLAlchemy engine for the app's shared DB access, or None."""
    from appmanager.db_access import get_db_engine as _impl

    return _impl(app_slug)


def refresh_db_engine(app_slug: str):
    """Dispose and re-fetch the app's DB engine (for rotation / permission changes)."""
    from appmanager.db_access import refresh_db_engine as _impl

    return _impl(app_slug)


def get_db_prefix(app_slug: str) -> Optional[str]:
    """Return the app's scoped table prefix, or None."""
    from appmanager.db_access import get_db_prefix as _impl

    return _impl(app_slug)


def get_auth_context(app_slug: str, headers: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Return a narrow read-only auth context (login state / display name / role)."""
    from appmanager.db_access import get_auth_context as _impl

    return _impl(app_slug, headers)
