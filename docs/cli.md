# CLI Reference

AppManager installs a global command line tool `appmanager` registered via `pyproject.toml`.

---

## Command Overview

```text
usage: appmanager [-h] [-v] {run,dev,seed,check-health,run-scheduled-tasks,list-apps,init,new-subapp,hooks,validate-subapp,export-app,reload-app,set-role,make-admin,list-users,list-roles,create-role} ...

AppManager CLI - Multi-tenant WSGI Portal & Sub-App Dispatcher

positional arguments:
  {run,dev,seed,check-health,run-scheduled-tasks,list-apps,init,new-subapp,hooks,validate-subapp,export-app,reload-app,set-role,make-admin,list-users,list-roles,create-role}
                        Available subcommands
    run                 Start the development WSGI dispatcher server
    dev                 Run a local development server for a specific sub-app
    seed                Seed the database with default sample sub-apps and extensions
    check-health        Run health checks across all registered sub-apps
    run-scheduled-tasks Run background scheduled cron jobs and maintenance
    list-apps           List all registered sub-apps and their status
    init                Bootstrap local directory with installed_apps/ and default configuration
    new-subapp          Scaffold a new standardized Flask sub-app or extension
    hooks               List registered UI slots and lifecycle hooks
    validate-subapp     Validate a sub-app folder or ZIP package
    export-app          Export an installed sub-app into a deployable ZIP
    reload-app          Clear WSGI module cache for an app
    set-role            Update or elevate a user role (admin or user)
    make-admin          Convenience shortcut to elevate a user to admin
    list-users          List all registered users and their roles
    list-roles          List all configured roles and member counts
    create-role         Create a new custom role
```

---

## Sub-App Scaffolding & Development

### `appmanager new-subapp <name>`
Generates a complete, ready-to-run standardized sub-application directory with `manifest.json`, `app.py`, `/health` route, and SDK integration.

```bash
# Standalone Flask sub-app
appmanager new-subapp "Customer Portal" --slug customer-portal --template basic

# RESTful JSON API
appmanager new-subapp "Payments API" --slug payments-api --template api

# UI Slot Extension Plugin
appmanager new-subapp "Custom Flair" --slug custom-flair --template extension

# Interactive HTMX Reactive App
appmanager new-subapp "Live Monitor" --slug live-monitor --template htmx

# Full sub-app with scheduled cron task
appmanager new-subapp "Data Warehouse" --slug data-warehouse --template full
```

**Options**:
- `--slug <slug>`: URL slug (defaults to sanitized name)
- `--output-dir <path>`: Destination folder
- `--template [basic|standalone|api|extension|htmx|full]`: Preset archetype

---

### `appmanager dev <slug>`
Runs a standalone local development server for a single sub-app with hot-reloading and injected mock authentication headers.

```bash
appmanager dev customer-portal --port 5001 --email admin@example.com --role admin
```

**Options**:
- `--host <ip>` (default: `127.0.0.1`)
- `-p, --port <port>` (default: `5001`)
- `--email <email>`: Mock user email for `X-AppManager-User-Email`
- `--role <role>`: Mock user role (`admin` or `user`)

---

### `appmanager hooks`
Inspects all registered UI slots (`user_badge`, `dashboard_widget`, `nav_item`, `head_assets`) and lifecycle event hooks currently active in the system.

```bash
appmanager hooks
```

---

### `appmanager validate-subapp <path_or_zip>`
Validates a sub-app folder or ZIP archive against AppManager standards (checks manifest schema, entrypoint accessibility, and python syntax) before deployment.

```bash
appmanager validate-subapp ./installed_apps/customer-portal
# Or validate archive:
appmanager validate-subapp ./customer-portal.zip
```

---

### `appmanager export-app <slug>`
Packages an installed sub-application folder into a clean, distributable ZIP file (excluding `__pycache__` and `.git`).

```bash
appmanager export-app customer-portal --output customer-portal-v1.zip
```

---

### `appmanager reload-app <slug>`
Invalidates the in-memory WSGI cache for a sub-app to trigger live reload without restarting the host server.

```bash
appmanager reload-app customer-portal
```

---

## Server & Task Automation

### `appmanager init`
Bootstraps an `installed_apps/` directory and `.env` template file in the current working directory.

```bash
appmanager init
```

---

### `appmanager run`
Starts the local development server wrapped in the `DynamicAppDispatcherMiddleware`.

**Options**:
- `--host <ip>` (default: `0.0.0.0` or `$HOST`)
- `-p, --port <port>` (default: `5000` or `$PORT`)
- `--no-reload` (disables auto-reloader)

```bash
appmanager run --port 8000
```

---

### `appmanager seed`
Populates the database with initial sample applications (`sample-counter`, `template-app`) and extension registrations (`extension-flairs`).

```bash
appmanager seed
```

---

### `appmanager check-health`
Runs health check routines across all active installed sub-apps and logs results to the database.

```bash
appmanager check-health
```

---

### `appmanager run-scheduled-tasks`
Executes declared cron tasks from `manifest.json`, runs health monitoring, and cleans up expired tokens and old health logs.

```bash
appmanager run-scheduled-tasks
```

---

### `appmanager list-apps`
Lists all applications in the database, their active state, auth requirements, and latest health check status.

```bash
appmanager list-apps
```

---

---

## App Installation, Updates & Dependency Management

### `appmanager install-git <url>`
Installs a sub-application from a remote Git repository with automated AST security pre-checks and dependency validation.

```bash
appmanager install-git https://github.com/user/my-app.git --name "My App" --slug my-app -y
```

**Options**:
- `--name <name>`: Human-readable app name
- `--slug <slug>`: URL slug
- `--entry-point <module:attr>`: Entry point string (default: `app:app`)
- `-y, --yes`: Skip interactive confirmation prompt

---

### `appmanager install-zip <path>`
Installs a sub-application from a local `.zip` package with automated AST security auditing.

```bash
appmanager install-zip ./package.zip --name "Widget App" -y
```

---

### `appmanager update <slug>`
Updates an installed sub-application in-place. For Git apps, it executes a `git pull`, audits code changes, and updates dependencies. For ZIP apps, pass `--zip <path>` to apply a replacement package.

```bash
# Update Git sub-app:
appmanager update analytics

# Upgrade ZIP sub-app:
appmanager update widget-app --zip ./widget-app-v2.zip
```

---

### `appmanager check-deps [slug] [--all] [--install]`
Inspects Python runtime compatibility, PEP 508 package requirements, and conflict detection across sub-apps.

```bash
# Inspect all sub-apps:
appmanager check-deps --all
# or:
appmanager check-deps all

# Inspect specific sub-app:
appmanager check-deps analytics

# Inspect and automatically install missing requirements:
appmanager check-deps --all --install
```

---

### `appmanager install-deps [slug] [--all]`
Installs required packages declared in sub-app `requirements.txt` into the configured environment (`singular` shared venv or `isolated` per-app `.venv`).

```bash
# Install dependencies for all apps:
appmanager install-deps --all

# Install for single app:
appmanager install-deps analytics
```


### `appmanager list-users`
Lists all registered users in the database, including their email, assigned role, activity status, and online presence.

```bash
appmanager list-users
```

---

### `appmanager list-roles`
Lists all system and custom RBAC roles with assigned member counts.

```bash
appmanager list-roles
```

---

### `appmanager create-role <name> [--slug <slug>] [--description <desc>]`
Creates a new custom RBAC role for permission assignment.

```bash
appmanager create-role "Billing Manager" --slug billing-mgr --description "Manages invoices"
```
