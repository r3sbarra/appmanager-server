# Changelog

All notable changes to **`appmanager-server`** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **SEO capabilities**: apps can declare SEO metadata in their manifest (`seo`
  block) which the host persists to `InstalledApp` and renders into served HTML.
  - New `InstalledApp` SEO columns (`seo_title`, `seo_description`, `seo_keywords`,
    `seo_canonical_url`, `seo_og_image`, `seo_robots`, `seo_json_ld`) + auto-migration.
  - `base.html` `head_meta` block renders portal SEO (title, description, keywords,
    canonical, OG, Twitter, robots, JSON-LD).
  - Dispatcher middleware injects app SEO into sub-app HTML `<head>` (no duplicate
    tags; auth apps forced to `noindex` when configured).
  - `/robots.txt` and `/sitemap.xml` routes (sitemap lists public apps).
  - Admin app detail page shows a read-only **SEO** panel.
- **Admin Settings page** (`/admin/settings`) with three sections:
  - **SEO**: master toggle, portal SEO fields, per-app override, auth noindex,
    sitemap toggle.
  - **Dashboard & Login**: login-required toggle, dashboard on/off, default-app
    redirect.
  - **Visibility**: show/hide login-required apps; adaptive button labels
    (Login / Permission Required / Launch).
- **Host settings store**: new `HostSetting` model + `host_settings` table and
  `appmanager.host_settings` helpers (typed get/set with canonical defaults).

---

## [0.4.0] - 2026-08-28

### Added
- **Automatic Sub-App Root Module Resolution**: Automatically prepends sub-app root directory (`app_dir`) to `sys.path` during dynamic module loading in `load_wsgi_app_from_path`, enabling seamless relative/module imports within sub-app packages.

---

## [0.3.1] - 2026-08-28

### Fixed
- **App & Role Deletion Type-To-Confirm Modal**: Fixed modal validation logic and dynamic form URL in Admin Dashboard where typing the exact app/role slug did not enable the confirmation submission.

---

## [0.3.0] - 2026-08-28

### Added
- **Dependency & Environment Conflict Engine (`appmanager.dependency_manager`)**:
  - Python runtime version verification comparing manifest `requires_python` against `platform.python_version()`.
  - PEP 508 requirement analyzer checking active environment distributions for satisfied packages, packages to install, and conflicts against core host frameworks (`Flask`, `SQLAlchemy`, etc.).
  - Shared dependency preservation analyzer protecting packages needed by other sub-apps when uninstalling.
- **Configurable Virtual Environment Modes (`APP_VENV_MODE`)**:
  - `"singular"` (Default): Single shared host environment.
  - `"isolated"`: Per-app isolated virtual environment (`.venv/`).
- **One-Click Sub-App Updates & Replacements**:
  - Git Update action (`POST /admin/apps/<id>/update-git`) pulling remote changes with automated AST security scan, dependency sync, and cache reload.
  - In-place ZIP replacement (`POST /admin/apps/<id>/precheck-replace-zip` -> `confirm-replace-zip`) with atomic rollback protection.
- **CLI Commands**:
  - `appmanager update <slug> [--zip <path>]`
  - `appmanager check-deps [slug] [--all] [--install]`
  - `appmanager install-deps [slug] [--all]`
- **Nested AppManager Portals**: Support for nesting AppManager instances as sub-apps within parent AppManager instances with recursive dispatching.

---

## [0.2.1] - 2026-08-28

### Added
- **Configurable Dev Magic Login (`ALLOW_DEV_MAGIC_LOGIN`)**: Prevents showing one-click developer login buttons in production when SMTP is unconfigured unless explicitly permitted.
- **Configurable Admin Role Provisioning (`ADMIN_EMAILS` & `FIRST_USER_IS_ADMIN`)**:
  - `ADMIN_EMAILS`: Specify comma-separated list of admin email addresses.
  - `FIRST_USER_IS_ADMIN`: Controls whether the very first registered user in a blank database receives the admin role (defaults to `true`).

### Changed
- Improved error messaging on `/auth/login` when SMTP is unconfigured.
- Expanded authentication test suite.

---

## [0.2.0] - 2026-08-28

### Added
- Initial PyPI release under package name **`appmanager-server`**.
- CLI commands and aliases: `appmanager-server`, `appmgr-server`, `appmanager`, and `appmgr`.
- AST Security Scanner (`appmanager.security_scanner.SecurityScanner`) for ZIP and Git package pre-installation checks.
- Python 3.10-3.13 support.
- PyPI Trusted Publishing via GitHub Actions OIDC.
