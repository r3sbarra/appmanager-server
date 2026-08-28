from datetime import datetime, timezone


def run_maintenance():
    """
    Scheduled cron task executed periodically by AppManager CLI (run-scheduled-tasks).
    """
    now = datetime.now(timezone.utc).isoformat()
    print(f"[TEMPLATE-APP CRON] Maintenance job executed at {now}")

    # Send telemetry report to host
    try:
        from appmanager.bridge import report_event

        report_event(
            "template-app", "cron_maintenance_run", {"timestamp": now, "status": "success"}
        )
    except Exception as e:
        print(f"[TEMPLATE-APP CRON WARNING] Telemetry reporting failed: {e}")
