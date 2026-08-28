# Python API Reference

AppManager exposes programmatic interfaces for embedding within larger Python frameworks, developing sub-apps, registering extension hooks, integrating telemetry, and managing signals.

---

## Top-Level Exports

```python
import appmanager

print(appmanager.__version__)  # '0.2.0'
```

### `AppManager(app=None, db=None)`
The standard Flask extension class for AppManager.

```python
from flask import Flask
from appmanager import AppManager

app = Flask(__name__)
manager = AppManager(app)

# Or using factory pattern:
manager = AppManager()
manager.init_app(app)

# Programmatic management
subapps = manager.get_apps()
manager.clear_cache("my-app-slug")
```

---

## Developer SDK (`appmanager.sdk`)

The Developer SDK provides high-level utilities for sub-app developers:

### `AppManagerClient(app_slug=None)`

```python
from appmanager.sdk import AppManagerClient

client = AppManagerClient("my-subapp")

# 1. User Context
user = client.get_current_user(request.headers)
# -> {'id': 1, 'email': 'user@example.com', 'role': 'admin', 'is_admin': True}


# 2. View Authentication Decorator
@app.route("/admin-only")
@client.require_auth(role="admin")
def admin_view():
    return "Secret admin area"


# 3. Telemetry & Metrics
client.report_event("report_exported", {"format": "pdf"})
client.report_metric("query_time_ms", 14.5, unit="ms")

# 4. App Settings
api_key = client.get_setting("api_key", default="demo")

# 5. Extension Key-Value Data Store
client.set_data("user_pref", user["id"], {"theme": "dark"})
prefs = client.get_data("user_pref", user["id"])

# 6. Hook & Slot Registration
client.register_slot("dashboard_widget", my_widget_fn, priority=10)
client.register_hook("on_app_installed", my_hook_fn)
```

---

## Hook & UI Slot System (`appmanager.hooks`)

Pluggable registry for UI mount points and lifecycle hooks:

```python
from appmanager.hooks import register_slot, render_slot, register_hook, trigger_hook

# Register a UI slot renderer
register_slot("user_badge", lambda user_id: "<span>⭐ VIP</span>", priority=5, app_slug="vip-app")

# Render all callbacks in priority order (returns Jinja Markup)
html = render_slot("user_badge", user_id=42)

# Register a lifecycle event handler
register_hook("on_user_login", lambda user: print(f"User {user.email} logged in!"))

# Trigger a lifecycle hook
trigger_hook("on_user_login", user=current_user)
```

---

## Signals & Event Hooks (`appmanager.signals`)

AppManager uses [Blinker](https://blinker.readthedocs.io/) to provide decoupled lifecycle signals:

```python
from appmanager.signals import (
    subapp_installed,
    subapp_uninstalled,
    subapp_reloaded,
    health_check_completed,
    health_check_failed,
    telemetry_received,
)


# Listen to sub-app installations
@subapp_installed.connect
def on_subapp_installed(sender, app_slug, source_type, app_id, **kwargs):
    print(f"Installed app {app_slug} via {source_type}")


# Listen to health check failures (for Slack/Discord notifications)
@health_check_failed.connect
def on_health_failure(sender, app_slug, status, details, **kwargs):
    print(f"ALERT: App {app_slug} status is {status}: {details}")


# Listen to telemetry events
@telemetry_received.connect
def on_telemetry(sender, app_slug, event_type, data, **kwargs):
    print(f"Telemetry from {app_slug}: {event_type} -> {data}")
```

---

## REST API Reference (`/api/v1`)

AppManager includes headless REST endpoints for automation and monitoring:

| Endpoint | Method | Description | Auth Required |
| :--- | :--- | :--- | :--- |
| `/api/v1/health` | `GET` | System & DB health probe | Public |
| `/api/v1/apps` | `GET` | List installed apps & health | Public |
| `/api/v1/apps/<slug>` | `GET` | Get app details & health | Public |
| `/api/v1/apps/install` | `POST` | Install via Git or multipart ZIP | API Key / Bearer |
| `/api/v1/apps/<slug>` | `DELETE` | Uninstall app | API Key / Bearer |
| `/api/v1/apps/<slug>/health-check` | `POST` | Trigger instant health check | API Key / Bearer |
| `/api/v1/apps/<slug>/reload` | `POST` | Clear module cache (live reload) | API Key / Bearer |
| `/api/v1/metrics` | `GET` | Get telemetry summary | Public |

Authenticate via `X-API-Key: <key>` or `Authorization: Bearer <admin_jwt>`.
