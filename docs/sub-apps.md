# Sub-App & Extension Development Guide

AppManager dynamically hosts independent WSGI applications and extensions inside the `installed_apps/<slug>/` folder.

---

## Sub-App Manifest: Python-Native or `manifest.json`

AppManager automatically configures entry points, health check routes, settings schemas, UI slots, and scheduled cron jobs upon ZIP upload or Git clone.

You can declare this configuration **either** in Python code via the lightweight `appmanager-sdk` package or by writing a static `manifest.json`.

---

### Option A: Python-Native Manifest Builder (`pip install appmanager-sdk`)

Instead of writing JSON manually, install the lightweight SDK (`pip install appmanager-sdk`) and define your manifest directly in `app.py`:

```python
from flask import Flask, jsonify, request
from appmanager_sdk import AppManifest, Setting, AdminSection, ScheduledTask, AppManagerClient

app = Flask(__name__)
client = AppManagerClient("analytics-dashboard")

# Define manifest in Python with full type-safety and auto-completion
manifest = AppManifest(
    name="My Analytics Dashboard",
    slug="analytics-dashboard",
    version="1.0.0",
    description="Real-time metrics and data visualization sub-app.",
    author="Engineering Team",
    entry_point="app:app",
    health_check_path="/health",
    app_type="standalone",
    has_web_ui=True,
    requires_auth=True,
    settings=[
        Setting(
            key="api_key",
            type="string",
            default="demo-key-12345",
            description="External ingestion API key.",
        ),
        Setting(
            key="refresh_interval_sec", type="integer", default=60, label="Polling Interval (s)"
        ),
    ],
    ui_slots=["dashboard_widget"],
    scheduled_tasks=[
        ScheduledTask(name="hourly_cache_warm", entry_point="tasks:warm_cache", frequency="hourly")
    ],
)


@app.route("/")
@client.require_auth(role="user")
def home():
    user = client.get_current_user(request.headers)
    api_key = client.get_setting("api_key", default="demo-key")
    return f"<h1>Hello, {user['email'] if user else 'Guest'}</h1>"


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "slug": "analytics-dashboard", "version": "1.0.0"})


if __name__ == "__main__":
    import sys

    if "--generate-manifest" in sys.argv or "generate-manifest" in sys.argv:
        manifest.cli()
    else:
        app.run(port=5001, debug=True)
```

#### Generating `manifest.json`:
* **Via CLI**: `appmanager-sdk generate app:manifest` or `appmanager-sdk generate app.py`
* **Via Python flag**: `python app.py --generate-manifest`
* **Automatic Discovery**: AppManager host auto-discovers Python manifests in `app.py` even if `manifest.json` hasn't been pre-generated!

---

### Option B: Static `manifest.json`

You can also place a standard `manifest.json` at the root of your sub-app:

```json
{
  "name": "My Analytics Dashboard",
  "slug": "analytics-dashboard",
  "version": "1.0.0",
  "description": "Real-time metrics and data visualization sub-app.",
  "author": "Engineering Team",
  "entry_point": "app:app",
  "health_check_path": "/health",
  "app_type": "standalone",
  "has_web_ui": true,
  "requires_auth": true,
  "settings": {
    "api_key": {
      "type": "string",
      "default": "demo-key-12345",
      "description": "External ingestion API key."
    },
    "refresh_interval_sec": {
      "type": "number",
      "default": 60,
      "description": "Dashboard polling interval in seconds."
    }
  },
  "ui_slots": ["dashboard_widget"],
  "scheduled_tasks": [
    {
      "name": "hourly_cache_warm",
      "entry_point": "tasks:warm_cache",
      "frequency": "hourly"
    }
  ]
}
```

### Manifest Field Specification

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | string | **Yes** | Human-readable title of the application. |
| `slug` | string | **Yes** | URL-safe slug used for routing (`/apps/<slug>/`). |
| `version` | string | No | Semantic version string (`1.0.0`). |
| `description` | string | No | Short summary of the sub-app purpose. |
| `entry_point` | string | No | WSGI callable in `module:callable` format (defaults to `app:app`). |
| `health_check_path` | string | No | Endpoint evaluated by health monitoring (defaults to `/health`). |
| `app_type` | string | No | `standalone` (sub-app) or `extension` (modifies host portal). |
| `has_web_ui` | bool | No | Whether this app has a web interface (default: `true`). |
| `requires_auth` | bool | No | Whether authentication is enforced before accessing the app. |
| `settings` | object / array | No | Configurable settings schema edited from Admin Dashboard. |
| `admin_sections` | array | No | Custom admin panels mounted under `/admin/apps/<slug>/<id>`. |
| `ui_slots` | array | No | List of UI slots this extension mounts to. |
| `scheduled_tasks` | array | No | List of background cron routines. |
| `seo` | object | No | Declarative SEO metadata (see below). |

#### SEO metadata (`seo`)

Apps can declare SEO metadata that the host renders into the served HTML `<head>`
and uses for `robots.txt` / `sitemap.xml`. All fields are optional:

| Field | Type | Description |
| :--- | :--- | :--- |
| `title` | string | Overrides `<title>`; falls back to `name`. |
| `description` | string | Meta description. |
| `keywords` | array | Meta keywords. |
| `canonical_url` | string | Canonical link href. |
| `og_title` / `og_description` / `og_image` / `og_type` | string | Open Graph tags. |
| `twitter_card` / `twitter_image` | string | Twitter card tags. |
| `robots` | string | `index,follow`, `noindex,nofollow`, etc. |
| `json_ld` | object | Raw JSON-LD structured data. |

Auth-required apps default to `noindex` (configurable in **Admin → Settings → SEO**).
The host injects these tags into the sub-app's HTML only when the app declares SEO
and the tag isn't already present.

---

## Developing with the AppManager SDK (`appmanager.sdk`)

Sub-apps can use `AppManagerClient` to interact with the host environment seamlessly:

```python
from flask import Flask, jsonify, request
from appmanager.sdk import AppManagerClient

app = Flask(__name__)
client = AppManagerClient("analytics-dashboard")


@app.route("/")
@client.require_auth(role="user")
def home():
    user = client.get_current_user(request.headers)
    api_key = client.get_setting("api_key", default="demo-key")

    # Report telemetry back to AppManager host
    client.report_event("page_view", {"path": "/"})
    client.report_metric("active_sessions", 1)

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Analytics Dashboard</title></head>
    <body style="font-family: sans-serif; background: #0f172a; color: white; padding: 2rem;">
      <h1>Hello, {user["email"]}</h1>
      <p>AppManager Ingestion Key: <code>{api_key}</code></p>
    </body>
    </html>
    """


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "app_slug": "analytics-dashboard", "version": "1.0.0"})
```

---

## UI Slots & Extension Hooks (`appmanager.hooks`)

Extensions can register HTML components to mount points on the host portal:

```python
from markupsafe import Markup
from appmanager.sdk import AppManagerClient

client = AppManagerClient("custom-flair-ext")


def render_user_badge(user_id):
    flair = client.get_data("user", user_id)
    if flair and flair.get("badge"):
        return Markup(f'<span class="badge">{flair["badge"]}</span>')
    return Markup("")


# Register to the 'user_badge' slot
client.register_slot("user_badge", render_user_badge, priority=10)
```

### Standard Available UI Slots

- `user_badge`: Rendered next to user names across user lists, profiles, and admin tables.
- `dashboard_widget`: Rendered as interactive cards on the main portal dashboard.
- `nav_item`: Rendered in the top navigation bar.
- `head_assets`: Injected into the `<head>` tag for custom CSS/JS.

---

## Local Sub-App Development Server

You can develop and debug sub-apps locally in isolation without launching the entire host portal:

```bash
appmanager dev analytics-dashboard --port 5001 --email dev@example.com --role admin
```

The dev server injects mock `X-AppManager-*` authentication headers into all requests so you can test authorization flows immediately.

---

## Sub-App Dependencies

If your sub-app requires external libraries, include a standard `requirements.txt` file in the sub-app folder. When uploaded via ZIP in the Admin UI or pulled from Git, dependencies are automatically verified.
