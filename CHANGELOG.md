# Changelog

All notable changes to **`appmanager-server`** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
