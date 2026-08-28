import json
import os
import time
from typing import List, Tuple

from werkzeug.test import Client
from werkzeug.wrappers import Response

from appmanager.admin.app_installer import load_wsgi_app_from_path, parse_manifest
from appmanager.database import db
from appmanager.models import AppHealthLog, InstalledApp
from appmanager.signals import health_check_completed, health_check_failed


def check_app_health(app_record: InstalledApp) -> AppHealthLog:
    """
    Evaluates health status of a single installed sub-app and logs the result.
    Fires health_check_completed and health_check_failed signals.
    """
    target_dir = (
        os.path.join(db.app.config["INSTALLED_APPS_DIR"], app_record.slug)
        if hasattr(db, "app")
        else None
    )
    if not target_dir:
        from flask import current_app

        target_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], app_record.slug)

    if not os.path.exists(target_dir):
        log = AppHealthLog(
            app_id=app_record.id,
            status="unhealthy",
            response_time_ms=0.0,
            details=json.dumps(
                {"error": f"App directory for '{app_record.slug}' missing on disk."}
            ),
        )
        db.session.add(log)
        db.session.commit()
        try:
            health_check_completed.send(
                None, app_slug=app_record.slug, status="unhealthy", response_time_ms=0.0
            )
            health_check_failed.send(
                None, app_slug=app_record.slug, status="unhealthy", details="App directory missing"
            )
        except Exception:
            pass
        return log

    manifest = parse_manifest(target_dir)
    health_path = manifest.get("health_check_path", "/health") if manifest else "/health"
    entry_point = (
        manifest.get("entry_point", app_record.entry_point) if manifest else app_record.entry_point
    )

    start_time = time.time()
    try:
        sub_app_obj = load_wsgi_app_from_path(target_dir, entry_point)
        wsgi_callable = getattr(sub_app_obj, "wsgi_app", sub_app_obj)

        # Check if sub-app exports a direct get_health() function
        if hasattr(sub_app_obj, "get_health") and callable(getattr(sub_app_obj, "get_health")):
            res = sub_app_obj.get_health()
            elapsed_ms = (time.time() - start_time) * 1000
            status = res.get("status", "healthy") if isinstance(res, dict) else "healthy"
            log = AppHealthLog(
                app_id=app_record.id,
                status=status,
                response_time_ms=elapsed_ms,
                details=json.dumps(res) if isinstance(res, dict) else str(res),
            )
        else:
            # Issue internal WSGI request to health endpoint
            client = Client(wsgi_callable, Response)
            res = client.get(health_path)
            elapsed_ms = (time.time() - start_time) * 1000

            if res.status_code == 200:
                status = "healthy"
                try:
                    data = res.get_json() or res.get_data(as_text=True)
                except Exception:
                    data = res.get_data(as_text=True)
                details = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
            else:
                status = "degraded" if res.status_code < 500 else "unhealthy"
                details = json.dumps(
                    {"http_status": res.status_code, "body": res.get_data(as_text=True)}
                )

            log = AppHealthLog(
                app_id=app_record.id, status=status, response_time_ms=elapsed_ms, details=details
            )

    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        status = "unhealthy"
        log = AppHealthLog(
            app_id=app_record.id,
            status="unhealthy",
            response_time_ms=elapsed_ms,
            details=json.dumps({"error": str(e)}),
        )

    db.session.add(log)
    db.session.commit()

    try:
        health_check_completed.send(
            None, app_slug=app_record.slug, status=log.status, response_time_ms=log.response_time_ms
        )
        if log.status in ("degraded", "unhealthy"):
            health_check_failed.send(
                None, app_slug=app_record.slug, status=log.status, details=log.details
            )
    except Exception:
        pass

    return log


def check_all_apps_health() -> List[Tuple[str, str, float]]:
    """
    Runs health checks across all active registered sub-apps.
    """
    apps = InstalledApp.query.filter_by(is_active=True).all()
    results = []
    for app_record in apps:
        log = check_app_health(app_record)
        results.append((app_record.slug, log.status, log.response_time_ms))
    return results
