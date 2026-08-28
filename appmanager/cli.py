import argparse
import json
import os
import sys
from datetime import datetime, timezone

from appmanager import __version__, create_app, create_dispatchable_app
from appmanager.admin.app_installer import (
    export_app_to_zip,
    load_wsgi_app_from_path,
    parse_manifest,
    sanitize_slug,
    validate_subapp_package,
)
from appmanager.database import db
from appmanager.health import check_all_apps_health
from appmanager.models import AppHealthLog, InstalledApp, MagicLinkToken, Role, User


def run_health_checks(app=None):
    """
    Runs health checks across all registered active sub-apps.
    """
    if app is None:
        app = create_app()
    with app.app_context():
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting sub-app health checks...")
        results = check_all_apps_health()
        for slug, status, time_ms in results:
            print(f"  -> App '{slug}': Status={status.upper()}, ResponseTime={time_ms:.2f}ms")
        print("Health checks completed successfully.\n")


def run_scheduled_tasks(app=None):
    """
    Executes sub-app cron routines declared in manifest.json and performs system cleanup.
    Designed to be run as a PythonAnywhere Scheduled Task or system cron.
    """
    if app is None:
        app = create_app()
    with app.app_context():
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Executing scheduled tasks & maintenance..."
        )

        # 1. Run sub-app scheduled cron tasks
        apps = InstalledApp.query.filter_by(is_active=True).all()
        for app_record in apps:
            target_dir = os.path.join(app.config["INSTALLED_APPS_DIR"], app_record.slug)
            if not os.path.exists(target_dir):
                continue
            manifest = parse_manifest(target_dir)
            if not manifest or "scheduled_tasks" not in manifest:
                continue

            scheduled = manifest["scheduled_tasks"]
            if isinstance(scheduled, list):
                for task_info in scheduled:
                    task_name = task_info.get("name", "unnamed_task")
                    entry_point = task_info.get("entry_point")
                    if not entry_point or ":" not in entry_point:
                        continue
                    module_name, func_name = entry_point.split(":")
                    try:
                        load_wsgi_app_from_path(target_dir, app_record.entry_point)
                        mod_path = os.path.join(target_dir, f"{module_name}.py")
                        if os.path.exists(mod_path):
                            import importlib.util

                            spec = importlib.util.spec_from_file_location(
                                f"cron_{app_record.slug}_{module_name}", mod_path
                            )
                            mod = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(mod)
                            if hasattr(mod, func_name) and callable(getattr(mod, func_name)):
                                print(
                                    f"  -> Executing scheduled task '{task_name}' for app '{app_record.slug}'..."
                                )
                                getattr(mod, func_name)()
                    except Exception as e:
                        print(
                            f"  [ERROR] Scheduled task '{task_name}' in app '{app_record.slug}' failed: {e}"
                        )

        # 2. Run Health Checks
        run_health_checks(app)

        # 3. Perform maintenance & database cleanup
        now = datetime.now(timezone.utc)
        expired_count = MagicLinkToken.query.filter(MagicLinkToken.expires_at < now).delete()

        # Keep only latest 100 health logs per app
        for app_record in apps:
            old_logs = (
                AppHealthLog.query.filter_by(app_id=app_record.id)
                .order_by(AppHealthLog.checked_at.desc())
                .offset(100)
                .all()
            )
            for log in old_logs:
                db.session.delete(log)

        db.session.commit()
        print(f"Maintenance completed. Purged {expired_count} expired tokens.\n")


def list_apps(app=None):
    """
    Lists all registered applications and their status.
    """
    if app is None:
        app = create_app()
    with app.app_context():
        apps = InstalledApp.query.all()
        print(f"\nRegistered Applications ({len(apps)} total):")
        print("-" * 65)
        for a in apps:
            latest_health = (
                AppHealthLog.query.filter_by(app_id=a.id)
                .order_by(AppHealthLog.checked_at.desc())
                .first()
            )
            status_str = latest_health.status.upper() if latest_health else "UNKNOWN"
            auth_str = "Protected" if a.requires_auth else "Public"
            default_str = " [DEFAULT]" if a.is_default else ""
            active_str = "Active" if a.is_active else "INACTIVE"
            print(
                f" - [{a.id}] {a.name} (slug: {a.slug}){default_str} | Status: {active_str} | Auth: {auth_str} | Health: {status_str}"
            )
        print("-" * 65 + "\n")


def set_user_role(email: str, role: str = "admin", app=None) -> int:
    """
    Elevates or updates a user's role via CLI.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        clean_email = email.strip().lower()
        user = User.query.filter_by(email=clean_email).first()
        if not user:
            user = User(
                email=clean_email,
                name=clean_email.split("@")[0].capitalize(),
                role=role.strip().lower(),
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            print(
                f"✅ Created user '{user.email}' (ID: {user.id}) with role: {user.role.upper()}\n"
            )
            return 0

        user.role = role.strip().lower()
        db.session.commit()
        print(
            f"✅ Successfully updated user '{user.email}' (ID: {user.id}) role to: {user.role.upper()}\n"
        )
        return 0


def list_users_cli(app=None) -> int:
    """
    Lists all registered users and their roles via CLI.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        users = User.query.order_by(User.created_at.desc()).all()
        print(f"\nRegistered Users ({len(users)} total):")
        print("-" * 75)
        for u in users:
            online_str = "Online" if u.is_online() else "Offline"
            active_str = "Active" if u.is_active else "Suspended"
            print(
                f" - [{u.id}] {u.email} | Name: {u.name or '-'} | Role: {u.role.upper()} | Status: {active_str} ({online_str})"
            )
        print("-" * 75 + "\n")
        return 0


def list_roles_cli(app=None) -> int:
    """
    Lists all configured roles and member counts via CLI.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
        print(f"\nConfigured Roles ({len(roles)} total):")
        print("-" * 75)
        for r in roles:
            m_count = User.query.filter_by(role=r.slug).count()
            type_str = "System" if r.is_system else "Custom"
            print(
                f" - [{r.id}] {r.name} (slug: {r.slug}) | Type: {type_str} | Members: {m_count} | {r.description or ''}"
            )
        print("-" * 75 + "\n")
        return 0


def create_role_cli(name: str, slug: str = None, description: str = None, app=None) -> int:
    """
    Creates a new custom role via CLI.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        clean_slug = sanitize_slug(slug or name)
        existing = Role.query.filter((Role.slug == clean_slug) | (Role.name == name)).first()
        if existing:
            print(f"❌ Role with name '{name}' or slug '{clean_slug}' already exists.\n")
            return 1

        new_role = Role(name=name, slug=clean_slug, description=description or "", is_system=False)
        db.session.add(new_role)
        db.session.commit()
        print(f"✅ Successfully created role '{new_role.name}' (slug: {new_role.slug})\n")
        return 0


def seed_db(app=None):
    """
    Seeds database with initial reference apps and extensions.
    """
    if app is None:
        app = create_app()
    with app.app_context():
        counter_app = InstalledApp.query.filter_by(slug="sample-counter").first()
        if not counter_app:
            counter_app = InstalledApp(
                name="Counter Sub-App",
                slug="sample-counter",
                description="Interactive Flask counter demonstration app",
                source_type="zip",
                source_url="sample-counter.zip",
                entry_point="app:app",
                is_active=True,
            )
            db.session.add(counter_app)
            print("[SEED] Registered sample-counter app in database.")

        template_app = InstalledApp.query.filter_by(slug="template-app").first()
        if not template_app:
            template_app = InstalledApp(
                name="Template Reference App",
                slug="template-app",
                description="Standardized Flask template application demonstrating health check, bridge telemetry, and scheduled cron task integration.",
                source_type="zip",
                source_url="template-app.zip",
                entry_point="app:app",
                is_active=True,
            )
            db.session.add(template_app)
            print("[SEED] Registered template-app in database.")

        extension_app = InstalledApp.query.filter_by(slug="extension-flairs").first()
        if not extension_app:
            extension_app = InstalledApp(
                name="User Flairs Extension",
                slug="extension-flairs",
                description="Adds customizable user titles, flairs, and badges to member profiles and admin views.",
                source_type="zip",
                source_url="extension-flairs.zip",
                entry_point="extension:extension",
                app_type="extension",
                target_app="appmanager",
                has_web_ui=False,
                is_active=True,
            )
            db.session.add(extension_app)
            print("[SEED] Registered extension-flairs in database.")
        else:
            extension_app.app_type = "extension"
            extension_app.target_app = "appmanager"
            extension_app.has_web_ui = False
            print("[SEED] Updated extension-flairs DB record.")

        db.session.commit()
        print("[SEED] Database seeding complete.\n")


def init_project():
    """
    Initializes local directory with installed_apps/ folder and .env template if not present.
    """
    os.makedirs("installed_apps", exist_ok=True)
    print("Created ./installed_apps/ directory.")
    if not os.path.exists(".env"):
        env_sample = (
            "# AppManager Environment Configuration\n"
            "SECRET_KEY=dev-secret-key-change-in-production-min-32-chars\n"
            "JWT_SECRET=dev-jwt-secret-key-change-in-production-min-32-chars\n"
            "APP_BASE_URL=http://localhost:5000\n"
            "PORT=5000\n"
        )
        with open(".env", "w") as f:
            f.write(env_sample)
        print("Created default .env file.")
    print("Initialization complete. Run `appmanager seed` followed by `appmanager run` to start.")


def new_subapp(name: str, slug: str = None, output_dir: str = None, template: str = "basic"):
    """
    Scaffolds a new AppManager sub-application or extension.
    Templates: 'basic', 'api', 'extension', 'htmx', 'full'
    """
    slug = sanitize_slug(slug or name)
    dest_dir = output_dir or os.path.join("installed_apps", slug)
    os.makedirs(dest_dir, exist_ok=True)

    manifest_data = {
        "name": name,
        "slug": slug,
        "version": "1.0.0",
        "description": f"Standardized sub-application for {name}.",
        "entry_point": "app:app",
        "health_check_path": "/health",
        "app_type": "extension" if template == "extension" else "standalone",
        "has_web_ui": True,
    }

    if template == "api":
        manifest_data["description"] = f"RESTful JSON API backend for {name}."
        manifest_data["entry_point"] = "app:app"
        app_code = f"""from flask import Flask, jsonify, request
from appmanager.sdk import AppManagerClient

app = Flask(__name__)
client = AppManagerClient('{slug}')

@app.route('/')
def root():
    user = client.get_current_user(request.headers)
    return jsonify({{
        'message': 'Welcome to {name} API',
        'app': '{slug}',
        'user': user,
        'endpoints': ['/api/v1/status', '/api/v1/metrics']
    }})

@app.route('/api/v1/status')
def status():
    return jsonify({{'status': 'operational', 'app': '{slug}', 'version': '1.0.0'}})

@app.route('/api/v1/events', methods=['POST'])
def record_event():
    payload = request.get_json(silent=True) or {{}}
    client.report_event('custom_api_event', payload)
    return jsonify({{'recorded': True, 'data': payload}})

@app.route('/health')
def health():
    return jsonify({{'status': 'healthy', 'app': '{slug}'}})

if __name__ == '__main__':
    app.run(port=5001, debug=True)
"""

    elif template == "extension":
        manifest_data["target_app"] = "appmanager"
        manifest_data["ui_slots"] = ["dashboard_widget", "user_badge"]
        manifest_data["settings"] = {
            "banner_text": {
                "type": "string",
                "default": f"Welcome to {name}!",
                "description": "Message displayed on the host dashboard widget.",
            },
            "accent_color": {
                "type": "string",
                "default": "#38bdf8",
                "description": "Primary accent color hex.",
            },
        }
        app_code = f'''from flask import Flask, jsonify, render_template_string, request
from markupsafe import Markup
from appmanager.sdk import AppManagerClient

app = Flask(__name__)
client = AppManagerClient('{slug}')

def render_dashboard_widget(user=None):
    banner = client.get_setting('banner_text', default='Welcome to {name}!')
    color = client.get_setting('accent_color', default='#38bdf8')
    html = f"""
    <div style="background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between;">
        <div>
            <div style="font-weight: 700; font-size: 1.1rem; color: {{color}};">✨ {{banner}}</div>
            <div style="color: #94a3b8; font-size: 0.85rem; margin-top: 0.25rem;">Custom extension widget mounted via AppManager Hook System.</div>
        </div>
        <a href="/apps/{slug}/" style="background: #6366f1; color: white; padding: 0.5rem 1rem; border-radius: 8px; text-decoration: none; font-size: 0.85rem; font-weight: 600;">Manage Extension &rarr;</a>
    </div>
    """
    return Markup(html)

client.register_slot('dashboard_widget', render_dashboard_widget, priority=5)

@app.route('/')
def index():
    user = client.get_current_user(request.headers)
    banner = client.get_setting('banner_text', default='Welcome to {name}!')
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{name} Extension</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: white; padding: 40px; display: flex; justify-content: center; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 32px; max-width: 600px; width: 100%; border: 1px solid #334155; }}
            h1 {{ color: #38bdf8; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🧩 {name} Extension</h1>
            <p>Active banner: <strong>{{banner}}</strong></p>
            <p>Current user: <strong>{{user.get('email') if user else 'Anonymous'}}</strong></p>
            <br>
            <a href="/" style="color: #94a3b8; text-decoration: none;">&larr; Return to Dashboard</a>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return jsonify({{'status': 'healthy', 'extension': '{slug}'}})
'''

    elif template == "htmx":
        app_code = f'''from flask import Flask, jsonify, render_template_string, request
from appmanager.sdk import AppManagerClient

app = Flask(__name__)
client = AppManagerClient('{slug}')

HTMX_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{name}</title>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; padding: 40px 20px; margin: 0; }}
        .card {{ background: #1e293b; border-radius: 16px; padding: 32px; max-width: 640px; width: 100%; border: 1px solid #334155; }}
        h1 {{ color: #38bdf8; margin-top: 0; }}
        .box {{ background: #0f172a; border: 1px solid #334155; padding: 16px; border-radius: 8px; margin: 16px 0; }}
        .btn {{ background: #6366f1; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; }}
        .btn:hover {{ background: #4f46e5; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>⚡ {name} (HTMX Reactive)</h1>
        <p>Interactive, server-driven reactive UI powered by HTMX.</p>

        <div id="live-area" class="box">
            <p>Click below to fetch dynamic live data from server without full reload:</p>
            <button class="btn" hx-get="live-data" hx-target="#live-area" hx-swap="innerHTML">Load Server Telemetry</button>
        </div>

        <br>
        <a href="/" style="color: #94a3b8; font-size: 0.9rem; text-decoration: none;">&larr; Return to AppManager Portal</a>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTMX_TEMPLATE)

@app.route('/live-data')
def live_data():
    from datetime import datetime
    client.report_event('htmx_data_refresh')
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""
    <div style="color: #4ade80; font-weight: 600;">✅ Server Response Generated at {{now}}</div>
    <p style="color: #94a3b8; font-size: 0.9rem;">Telemetric event dispatched to AppManager host.</p>
    <button class="btn" hx-get="live-data" hx-target="#live-area" hx-swap="innerHTML">Refresh Again</button>
    """

@app.route('/health')
def health():
    return jsonify({{'status': 'healthy', 'app': '{slug}', 'type': 'htmx'}})
'''

    else:
        # 'basic' or 'full' template
        if template == "full":
            manifest_data["scheduled_tasks"] = [
                {"name": "daily_cleanup", "entry_point": "tasks:run_cleanup", "frequency": "daily"}
            ]

        app_code = f'''from flask import Flask, jsonify, render_template_string, request
from appmanager_sdk import AppManagerClient, AppManifest, Setting

app = Flask(__name__)
client = AppManagerClient('{slug}')

# Declarative Sub-App Manifest
manifest = AppManifest(
    name="{name}",
    slug="{slug}",
    version="1.0.0",
    description="A modern sub-application for AppManager.",
    entry_point="app:app",
    health_check_path="/health",
    has_web_ui=True,
    requires_auth=True,
    ui_slots=["dashboard_widget"]
)

INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>{name}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px 20px; display: flex; justify-content: center; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 32px; max-width: 600px; width: 100%; border: 1px solid #334155; }}
        h1 {{ margin-top: 0; color: #38bdf8; }}
        .badge {{ background: #334155; padding: 4px 8px; border-radius: 6px; font-size: 0.85rem; }}
        .btn {{ background: #0284c7; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-block; margin-top: 16px; font-weight: 600; }}
        .btn:hover {{ background: #0369a1; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🚀 {name}</h1>
        <p>This sub-app is running under AppManager at <span class="badge">/apps/{slug}/</span></p>
        {{% if user %}}
        <p>Logged in as: <strong>{{{{ user.email }}}}</strong> (Role: {{{{ user.role }}}})</p>
        {{% else %}}
        <p>Browsing anonymously.</p>
        {{% endif %}}
        <a href="/" class="btn">Return to Portal</a>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    user = client.get_current_user(request.headers)
    client.report_event('page_view', {{'path': '/'}})
    return render_template_string(INDEX_HTML, user=user)

@app.route('/health')
def health():
    return jsonify({{'status': 'healthy', 'app': '{slug}', 'version': '1.0.0'}})

if __name__ == '__main__':
    import sys
    if '--generate-manifest' in sys.argv or 'generate-manifest' in sys.argv:
        manifest.cli()
    else:
        app.run(port=5001, debug=True)
'''

    # Write manifest.json
    with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # Write app.py
    with open(os.path.join(dest_dir, "app.py"), "w", encoding="utf-8") as f:
        f.write(app_code)

    # Write requirements.txt
    with open(os.path.join(dest_dir, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("Flask>=3.0.0\nappmanager-sdk>=0.1.0\n")

    # If template == 'full', write tasks.py
    if template == "full":
        tasks_code = """def run_cleanup():
    print("[TASK] Running background cleanup task...")
"""
        with open(os.path.join(dest_dir, "tasks.py"), "w", encoding="utf-8") as f:
            f.write(tasks_code)

    print(f"\n✅ Created new sub-app '{name}' (template: {template}) at: {dest_dir}")
    print(f"   Manifest: {os.path.join(dest_dir, 'manifest.json')}")
    print(f"   Entry:    {os.path.join(dest_dir, 'app.py')}\n")


def dev_subapp(
    slug: str,
    host: str = "127.0.0.1",
    port: int = 5001,
    user_email: str = "admin@example.com",
    user_role: str = "admin",
) -> int:
    """
    Runs a standalone local development server for a specific sub-app with mock user headers.
    """
    from werkzeug.serving import run_simple

    target_dir = os.path.join("installed_apps", slug)
    if not os.path.exists(target_dir):
        print(f"❌ Sub-app directory '{target_dir}' not found.")
        return 1

    manifest = parse_manifest(target_dir) or {}
    entry_point = manifest.get("entry_point", "app:app")

    print("\n=======================================================")
    print(f" AppManager Dev Server: '{slug}'")
    print(f" Running at: http://{host}:{port}/")
    print(f" Mock User:  {user_email} (Role: {user_role})")
    print("=======================================================\n")

    sub_app_obj = load_wsgi_app_from_path(target_dir, entry_point)
    raw_wsgi = getattr(sub_app_obj, "wsgi_app", sub_app_obj)

    def dev_mock_wrapper(environ, start_response):
        environ["HTTP_X_APPMANAGER_USER_ID"] = "1"
        environ["HTTP_X_APPMANAGER_USER_EMAIL"] = user_email
        environ["HTTP_X_APPMANAGER_USER_ROLE"] = user_role
        environ["HTTP_X_APPMANAGER_SUBAPP_SLUG"] = slug
        environ["APPMANAGER_SUBAPP_SLUG"] = slug
        return raw_wsgi(environ, start_response)

    run_simple(host, port, dev_mock_wrapper, use_reloader=True, use_debugger=True)
    return 0


def list_hooks_cli() -> int:
    """
    Lists registered UI slots and hooks across AppManager and active extensions.
    """
    from appmanager.hooks import hooks

    slots = hooks.get_registered_slots()
    hks = hooks.get_registered_hooks()

    print("\n--- AppManager Registered UI Slots ---")
    if not slots:
        print(" (No UI slots currently registered)")
    else:
        for slot_name, entries in slots.items():
            print(f" • Slot: '{slot_name}' ({len(entries)} listener(s))")
            for e in entries:
                print(f"    -> App: {e.get('app_slug') or 'core'}, Priority: {e.get('priority')}")

    print("\n--- AppManager Registered Lifecycle Hooks ---")
    if not hks:
        print(" (No lifecycle hooks currently registered)")
    else:
        for hook_name, entries in hks.items():
            print(f" • Hook: '{hook_name}' ({len(entries)} listener(s))")
            for e in entries:
                print(f"    -> App: {e.get('app_slug') or 'core'}, Priority: {e.get('priority')}")
    print("")
    return 0


def validate_subapp_cli(path_or_zip: str) -> int:
    """
    CLI command to validate a sub-app directory or zip file.
    """
    print(f"\n🔍 Validating sub-app at: {path_or_zip} ...")
    is_valid, errors, manifest = validate_subapp_package(path_or_zip)
    if is_valid:
        print("✅ PASSED: Package is valid.")
        print(f"   Name:        {manifest.get('name')}")
        print(f"   Slug:        {manifest.get('slug')}")
        print(f"   Entry Point: {manifest.get('entry_point', 'app:app')}")
        print(f"   Health Path: {manifest.get('health_check_path', '/health')}\n")
        return 0
    else:
        print("❌ FAILED: Validation errors found:")
        for err in errors:
            print(f"   - {err}")
        print("")
        return 1


def export_subapp_cli(slug: str, output: str = None, app=None) -> int:
    """
    CLI command to export an installed sub-app to a distributable zip.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        try:
            zip_path = export_app_to_zip(slug, output_path=output)
            print(f"✅ Successfully exported '{slug}' to: {zip_path}\n")
            return 0
        except Exception as e:
            print(f"❌ Export failed: {e}\n")
            return 1


def print_security_scan_results(scan_report) -> None:
    print("\n=======================================================")
    print(" 🛡️  AppManager Security Pre-Check Audit")
    print(f" Status:        [{scan_report.risk_level}]")
    print(
        f" Files Scanned: {scan_report.files_scanned} ({scan_report.py_files_scanned} Python files)"
    )
    print(f" Findings:      {len(scan_report.findings)}")
    print(f" Summary:       {scan_report.summary}")
    print("=======================================================")
    if not scan_report.findings:
        print(" ✅ Clean AST & dependency scan. No vulnerabilities found.\n")
    else:
        print("\n Detailed Findings:")
        for idx, f in enumerate(scan_report.findings, start=1):
            sev_tag = f"[{f.severity}]"
            loc = f"{f.file or 'unknown'}:{f.line}" if f.line else f"{f.file or 'unknown'}"
            print(f" {idx}. {sev_tag:<10} {f.category} ({loc})")
            print(f"    Issue:   {f.message}")
            if f.snippet:
                print(f"    Snippet: {f.snippet}")
            print("")


def install_git_cli(
    repo_url: str,
    name: str = None,
    slug: str = None,
    entry_point: str = None,
    yes: bool = False,
    app=None,
) -> int:
    """
    CLI command to pull and install a sub-app from a Git repository with security pre-checks.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        from appmanager.admin.app_installer import (
            cancel_staged_app,
            finalize_staged_installation,
            stage_git_repo,
        )

        print(f"\n🚀 Staging and pre-checking Git repository: {repo_url} ...")
        try:
            fallback_name = name or os.path.basename(repo_url.rstrip("/")).replace(".git", "")
            staging_id, scan_report, manifest = stage_git_repo(
                repo_url=repo_url,
                name=fallback_name,
                slug=slug,
                entry_point=entry_point,
            )
        except Exception as e:
            print(f"❌ Failed to clone and stage repository: {e}")
            return 1

        print_security_scan_results(scan_report)

        if not yes:
            try:
                ans = (
                    input("Pre-installation checks complete. Proceed with installation? [y/N]: ")
                    .strip()
                    .lower()
                )
            except (KeyboardInterrupt, EOFError):
                ans = "n"
            if ans != "y":
                cancel_staged_app(staging_id)
                print("🚫 Installation cancelled by user.\n")
                return 1

        try:
            installed = finalize_staged_installation(
                staging_id=staging_id,
                name=name,
                slug=slug,
                entry_point=entry_point,
            )
            print(f"✅ Successfully installed '{installed.name}' (/apps/{installed.slug})\n")
            return 0
        except Exception as err:
            print(f"❌ Installation failed: {err}\n")
            return 1


def install_zip_cli(
    zip_path: str,
    name: str = None,
    slug: str = None,
    entry_point: str = None,
    yes: bool = False,
    app=None,
) -> int:
    """
    CLI command to install a sub-app from a local ZIP archive with security pre-checks.
    """
    if not os.path.exists(zip_path):
        print(f"❌ ZIP file not found: {zip_path}")
        return 1

    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    with app.app_context():
        from werkzeug.datastructures import FileStorage

        from appmanager.admin.app_installer import (
            cancel_staged_app,
            finalize_staged_installation,
            stage_zip_file,
        )

        print(f"\n🚀 Staging and pre-checking ZIP package: {zip_path} ...")
        fallback_name = name or os.path.splitext(os.path.basename(zip_path))[0]

        try:
            with open(zip_path, "rb") as f:
                fs = FileStorage(stream=f, filename=os.path.basename(zip_path))
                staging_id, scan_report, manifest = stage_zip_file(
                    zip_file_storage=fs,
                    name=fallback_name,
                    slug=slug,
                    entry_point=entry_point,
                )
        except Exception as e:
            print(f"❌ Failed to extract and stage ZIP package: {e}")
            return 1

        print_security_scan_results(scan_report)

        if not yes:
            try:
                ans = (
                    input("Pre-installation checks complete. Proceed with installation? [y/N]: ")
                    .strip()
                    .lower()
                )
            except (KeyboardInterrupt, EOFError):
                ans = "n"
            if ans != "y":
                cancel_staged_app(staging_id)
                print("🚫 Installation cancelled by user.\n")
                return 1

        try:
            installed = finalize_staged_installation(
                staging_id=staging_id,
                name=name,
                slug=slug,
                entry_point=entry_point,
            )
            print(f"✅ Successfully installed '{installed.name}' (/apps/{installed.slug})\n")
            return 0
        except Exception as err:
            print(f"❌ Installation failed: {err}\n")
            return 1


def reload_subapp_cli(slug: str, app=None) -> int:
    """
    Clears cache for a sub-app.
    """
    if app is None:
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except Exception:
            app = create_app()

    if hasattr(app, "extensions") and "appmanager" in app.extensions:
        app.extensions["appmanager"].clear_cache(slug=slug)
    print(f"✅ In-memory cache cleared for sub-app '{slug}'.\n")
    return 0


def run_server(host="0.0.0.0", port=5000, reload=True):
    """
    Starts the WSGI Dynamic Dispatcher local development server.
    """
    from werkzeug.serving import run_simple

    application = create_dispatchable_app()
    print("\n=======================================================")
    print(f" AppManager v{__version__} starting at http://{host}:{port}")
    print("=======================================================\n")
    run_simple(host, int(port), application, use_reloader=reload, use_debugger=reload)


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="appmanager",
        description="AppManager CLI - Multi-tenant WSGI Portal & Sub-App Dispatcher",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # run command
    run_parser = subparsers.add_parser("run", help="Start the development WSGI dispatcher server")
    run_parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host to bind server (default: 0.0.0.0)",
    )
    run_parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=int(os.getenv("PORT", 5000)),
        help="Port to bind server (default: 5000)",
    )
    run_parser.add_argument(
        "--no-reload", dest="reload", action="store_false", help="Disable automatic code reloader"
    )
    run_parser.set_defaults(reload=True)

    # seed command
    subparsers.add_parser(
        "seed", help="Seed the database with default sample sub-apps and extensions"
    )

    # check-health command
    subparsers.add_parser("check-health", help="Run health checks across all registered sub-apps")

    # run-scheduled-tasks command
    subparsers.add_parser(
        "run-scheduled-tasks", help="Run background scheduled cron jobs and maintenance"
    )

    # list-apps command
    subparsers.add_parser("list-apps", help="List all registered sub-apps and their status")

    # init command
    subparsers.add_parser(
        "init", help="Bootstrap local directory with installed_apps/ and default configuration"
    )

    # new-subapp command
    new_parser = subparsers.add_parser(
        "new-subapp", help="Scaffold a new standardized Flask sub-app or extension"
    )
    new_parser.add_argument("name", help="Human-readable name of the sub-app")
    new_parser.add_argument("--slug", help="URL slug for sub-app (defaults to sanitized name)")
    new_parser.add_argument(
        "--output-dir", help="Target directory (defaults to ./installed_apps/<slug>)"
    )
    new_parser.add_argument(
        "--template",
        choices=["basic", "standalone", "api", "extension", "htmx", "full"],
        default="basic",
        help="Template preset (basic, api, extension, htmx, full)",
    )

    # dev command (Local Sub-App Development Server with mock auth)
    dev_parser = subparsers.add_parser(
        "dev", help="Run a local development server for a specific sub-app"
    )
    dev_parser.add_argument("slug", help="Slug of the sub-app to run")
    dev_parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    dev_parser.add_argument(
        "-p", "--port", type=int, default=5001, help="Port to bind (default: 5001)"
    )
    dev_parser.add_argument(
        "--email", default="admin@example.com", help="Mock authenticated user email"
    )
    dev_parser.add_argument(
        "--role", choices=["admin", "user"], default="admin", help="Mock authenticated user role"
    )

    # hooks command (List registered UI slots and hooks)
    subparsers.add_parser("hooks", help="List registered UI slots and lifecycle hooks")

    # validate-subapp command
    val_parser = subparsers.add_parser(
        "validate-subapp", help="Validate a sub-app folder or ZIP package"
    )
    val_parser.add_argument("path", help="Path to sub-app folder or .zip file")

    # export-app command
    exp_parser = subparsers.add_parser(
        "export-app", help="Export an installed sub-app into a deployable ZIP"
    )
    exp_parser.add_argument("slug", help="Slug of the installed app to export")
    exp_parser.add_argument("-o", "--output", help="Output ZIP path")

    # reload-app command
    rel_parser = subparsers.add_parser("reload-app", help="Clear WSGI module cache for an app")
    rel_parser.add_argument("slug", help="Slug of the installed app to reload")

    # set-role command (CLI User Elevation)
    role_parser = subparsers.add_parser(
        "set-role", help="Update or elevate a user role (admin or user)"
    )
    role_parser.add_argument("email", help="Email of the user to update")
    role_parser.add_argument(
        "--role", choices=["admin", "user"], default="admin", help="Role to assign (default: admin)"
    )

    # make-admin command (Convenience CLI Elevation)
    admin_parser = subparsers.add_parser(
        "make-admin", help="Convenience shortcut to elevate a user to admin"
    )
    admin_parser.add_argument("email", help="Email of the user to elevate to admin")

    # list-users command
    subparsers.add_parser("list-users", help="List all registered users and their roles")

    # list-roles command
    subparsers.add_parser("list-roles", help="List all configured roles and member counts")

    # create-role command
    role_create_parser = subparsers.add_parser("create-role", help="Create a new custom role")
    role_create_parser.add_argument("name", help="Display name of the role")
    role_create_parser.add_argument("--slug", help="Identifier slug (defaults to sanitized name)")
    role_create_parser.add_argument("--description", help="Description of the role")

    # install-git command
    git_inst_parser = subparsers.add_parser(
        "install-git", help="Pull and install a sub-app from Git with security pre-checks"
    )
    git_inst_parser.add_argument("url", help="Git repository URL")
    git_inst_parser.add_argument("--name", help="Display name of the application")
    git_inst_parser.add_argument("--slug", help="URL slug identifier")
    git_inst_parser.add_argument("--entry-point", help="Entry point string (e.g. app:app)")
    git_inst_parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt and install directly"
    )

    # install-zip command
    zip_inst_parser = subparsers.add_parser(
        "install-zip", help="Install a sub-app from a ZIP file with security pre-checks"
    )
    zip_inst_parser.add_argument("path", help="Path to ZIP archive")
    zip_inst_parser.add_argument("--name", help="Display name of the application")
    zip_inst_parser.add_argument("--slug", help="URL slug identifier")
    zip_inst_parser.add_argument("--entry-point", help="Entry point string (e.g. app:app)")
    zip_inst_parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt and install directly"
    )

    # generate-manifest command (delegates to appmanager-sdk)
    gen_man_parser = subparsers.add_parser(
        "generate-manifest", help="Generate manifest.json from a Python sub-app"
    )
    gen_man_parser.add_argument(
        "target", nargs="?", default="app:app", help="Target module or file (default: app:app)"
    )
    gen_man_parser.add_argument(
        "--out", "-o", default="manifest.json", help="Output manifest path (default: manifest.json)"
    )

    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 0

    if parsed.command == "run":
        run_server(host=parsed.host, port=parsed.port, reload=parsed.reload)
    elif parsed.command == "seed":
        seed_db()
    elif parsed.command == "check-health":
        run_health_checks()
    elif parsed.command == "run-scheduled-tasks":
        run_scheduled_tasks()
    elif parsed.command == "list-apps":
        list_apps()
    elif parsed.command == "init":
        init_project()
    elif parsed.command == "new-subapp":
        new_subapp(
            name=parsed.name,
            slug=parsed.slug,
            output_dir=parsed.output_dir,
            template=parsed.template,
        )
    elif parsed.command == "dev":
        return dev_subapp(
            slug=parsed.slug,
            host=parsed.host,
            port=parsed.port,
            user_email=parsed.email,
            user_role=parsed.role,
        )
    elif parsed.command == "hooks":
        return list_hooks_cli()
    elif parsed.command == "validate-subapp":
        return validate_subapp_cli(parsed.path)
    elif parsed.command == "install-git":
        return install_git_cli(
            repo_url=parsed.url,
            name=parsed.name,
            slug=parsed.slug,
            entry_point=parsed.entry_point,
            yes=parsed.yes,
        )
    elif parsed.command == "install-zip":
        return install_zip_cli(
            zip_path=parsed.path,
            name=parsed.name,
            slug=parsed.slug,
            entry_point=parsed.entry_point,
            yes=parsed.yes,
        )
    elif parsed.command == "export-app":
        return export_subapp_cli(slug=parsed.slug, output=parsed.output)
    elif parsed.command == "reload-app":
        return reload_subapp_cli(slug=parsed.slug)
    elif parsed.command == "set-role":
        return set_user_role(email=parsed.email, role=parsed.role)
    elif parsed.command == "make-admin":
        return set_user_role(email=parsed.email, role="admin")
    elif parsed.command == "list-users":
        return list_users_cli()
    elif parsed.command == "list-roles":
        return list_roles_cli()
    elif parsed.command == "create-role":
        return create_role_cli(name=parsed.name, slug=parsed.slug, description=parsed.description)
    elif parsed.command == "generate-manifest":
        from appmanager_sdk.cli import main as sdk_cli_main

        return sdk_cli_main(["generate", parsed.target, "--out", parsed.out])
    return 0


if __name__ == "__main__":
    sys.exit(main())
