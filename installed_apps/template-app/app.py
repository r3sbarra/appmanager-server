from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

app = Flask(__name__)
app.secret_key = "template-app-secret-key-32-bytes"

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Template Reference Sub-App</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 16px; border: 1px solid #334155; text-align: center; max-width: 460px; width: 100%; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
        h1 { font-size: 1.5rem; margin-top: 0; color: #38bdf8; }
        p { color: #94a3b8; font-size: 0.9rem; line-height: 1.5; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 9999px; font-size: 0.8rem; font-weight: 600; background: #059669; color: #ecfdf5; margin-bottom: 1rem; }
        .btn { padding: 0.75rem 1.5rem; border-radius: 8px; background: #0284c7; color: white; text-decoration: none; border: none; cursor: pointer; font-size: 1rem; font-weight: 600; display: inline-block; transition: background 0.2s; }
        .btn:hover { background: #0369a1; }
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">Standardized Sub-App</span>
        <h1>Template Reference App</h1>
        <p>Demonstrates Manifest specs, Standardized Health Contract (<code>/health</code>), in-process Telemetry, and PythonAnywhere Scheduled Task Cron integration.</p>
        <form method="POST" action="trigger-event">
            <button type="submit" class="btn">Send Telemetry Event</button>
        </form>
        <br><br>
        <a href="/" style="color: #94a3b8; font-size: 0.85rem; text-decoration: none;">&larr; Return to AppManager Dashboard</a>
    </div>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/health")
def health():
    """
    Standardized Health Check Contract Endpoint
    """
    return jsonify(
        {
            "status": "healthy",
            "app_slug": "template-app",
            "version": "1.0.0",
            "checks": {"database": "ok", "memory": "ok"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/trigger-event", methods=["POST"])
def trigger_event():
    """
    Example route reporting event telemetry to host via AppManagerBridge.
    """
    try:
        from appmanager.bridge import report_event, report_metric

        report_event("template-app", "user_action_click", {"ip": request.remote_addr})
        report_metric("template-app", "user_clicks", 1)
    except Exception as e:
        print(f"[TEMPLATE-APP] Telemetry report failed: {e}")
    return redirect(url_for("index"))
