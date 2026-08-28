# Security Policy

## Supported Versions

We provide security updates for the following versions of `appmanager-server`:

| Version | Supported          | Python Versions       |
|---------|--------------------|-----------------------|
| 0.2.x   | :white_check_mark: | >= 3.10, <= 3.13      |
| < 0.2.0 | :x:                | N/A                   |

---

## Reporting a Vulnerability

Please submit an issue directly to our [GitHub Issues Tracker](https://github.com/r3sbarra/appmanager-server/issues) with the following details:

- **Summary**: A clear and concise description of the security issue or unexpected behavior.
- **Reproduction**: Minimal steps to reproduce or proof-of-concept (PoC) code.
- **Environment**: Affected package version, Python version, database engine (SQLite/MySQL/PostgreSQL), and OS.
- **Impact & Mitigation**: Potential security impact and any proposed fixes or mitigations.

---

## Security Architecture & Best Practices

When operating `appmanager-server` in production or multi-tenant hosting environments:

### 1. In-Process Module Isolation & Pre-Installation Scanning
- AppManager includes an automated AST security pre-check scanner (`SecurityScanner`) that evaluates sub-application code before staging/finalizing installation from ZIP packages or Git repositories.
- The scanner detects dangerous patterns such as arbitrary code execution (`eval()`, `exec()`), shell command injection (`subprocess.Popen(..., shell=True)`), insecure deserialization (`pickle`, `marshal`), and forbidden binary files.
- Always review scanner findings before confirming installation of untrusted third-party apps.

### 2. Secret Key & Environment Configuration
- Set strong, random strings for `SECRET_KEY` and `JWT_SECRET_KEY` in production.
- When configuring HMAC identity verification between the host portal and distributed sub-apps, set `APPMANAGER_HEADER_SECRET` on both the host server and sub-app clients (`appmanager-sdk`).

### 3. Rate Limiting & Authentication
- Magic link authentication and admin endpoints are protected by in-memory / token-based rate limiting.
- The sub-app dispatcher sanitizes incoming HTTP headers (`X-AppManager-*`) from external clients to prevent header spoofing attacks.
