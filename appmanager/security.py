import hashlib
import hmac
import os
import re
import shutil
import stat
import time
import zipfile
from functools import wraps
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

from flask import abort, current_app, g, jsonify, request

# Security Constants
MAX_ZIP_EXTRACT_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_ZIP_FILE_COUNT = 1000
CSRF_TOKEN_MAX_AGE = 3600 * 4  # 4 hours

_rate_limit_records: Dict[str, list] = {}


def get_security_secret() -> str:
    """
    Retrieves the application secret key for HMAC operations.
    """
    return current_app.config.get("SECRET_KEY", "default-appmanager-security-secret")


def generate_csrf_token() -> str:
    """
    Generates a cryptographically signed HMAC CSRF token containing a timestamp.
    """
    secret = get_security_secret()
    timestamp = str(int(time.time()))
    # Use user ID if authenticated, else remote IP
    identity = str(getattr(g, "current_user_id", request.remote_addr or "anon"))
    signature = hmac.new(
        secret.encode("utf-8"), f"{identity}:{timestamp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{timestamp}:{signature}"


def validate_csrf_token(token: Optional[str]) -> bool:
    """
    Validates a submitted CSRF token.
    """
    if not token or ":" not in token:
        return False
    try:
        timestamp_str, signature = token.split(":", 1)
        token_time = int(timestamp_str)
        current_time = int(time.time())

        # Check expiration
        if current_time - token_time > CSRF_TOKEN_MAX_AGE or token_time > current_time + 60:
            return False

        secret = get_security_secret()
        identity = str(getattr(g, "current_user_id", request.remote_addr or "anon"))
        expected_sig = hmac.new(
            secret.encode("utf-8"), f"{identity}:{timestamp_str}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


def csrf_protect_required(f):
    """
    Decorator for route handlers requiring CSRF validation on non-safe HTTP methods.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token = (
                request.form.get("csrf_token")
                or request.headers.get("X-CSRFToken")
                or request.headers.get("X-CSRF-Token")
            )
            if not validate_csrf_token(token):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "CSRF validation failed", "success": False}), 400
                abort(400, description="CSRF validation failed. Please refresh and try again.")
        return f(*args, **kwargs)

    return decorated_function


def is_safe_redirect_url(target: Optional[str]) -> bool:
    """
    Ensures that a redirect target URL is safe and relative to the current host (prevents Open Redirect).
    """
    if not target:
        return False
    # Avoid javascript:, data:, or protocol-relative // URLs
    if target.startswith(("javascript:", "data:", "vbscript:")) or target.startswith("//"):
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def is_safe_repo_url(repo_url: str) -> bool:
    """
    Validates that a git repository URL is safe and does not contain option injection flags.
    """
    if not repo_url or repo_url.startswith("-"):
        return False
    pattern = r"^(https?|git|ssh)://[^\s]+$|^git@[^\s]+:[^\s]+$"
    return bool(re.match(pattern, repo_url.strip()))


def validate_entrypoint_path(app_dir: str, entry_point: str) -> Tuple[bool, str]:
    """
    Ensures an entrypoint string does not escape the app directory via directory traversal.
    Supports module names ('app'), dotted paths ('src.app'), and package paths ('appmanager').
    """
    if ":" in entry_point:
        module_name = entry_point.split(":", 1)[0]
    else:
        module_name = entry_point

    # Disallow parent references
    if ".." in module_name:
        return False, "Entrypoint module name cannot contain path separators or parent references."

    abs_app_dir = os.path.abspath(app_dir)

    # 1. Direct .py file path
    if module_name.endswith(".py"):
        cand_path = os.path.join(app_dir, module_name)
    else:
        cand_path = os.path.join(app_dir, f"{module_name}.py")
        if not os.path.exists(cand_path):
            # 2. Check dotted or nested path: e.g. src.app -> src/app.py or pkg/__init__.py
            parts = module_name.replace("\\", "/").replace(".", "/").split("/")
            cand_py = os.path.join(app_dir, *parts) + ".py"
            cand_init = os.path.join(app_dir, *parts, "__init__.py")
            if os.path.exists(cand_py):
                cand_path = cand_py
            elif os.path.exists(cand_init):
                cand_path = cand_init

    abs_module = os.path.abspath(cand_path)
    if not abs_module.startswith(abs_app_dir):
        return False, "Entrypoint file escapes application directory."

    return True, abs_module


def check_rate_limit(key: str, limit: int = 5, window_seconds: int = 60) -> bool:
    """
    In-memory sliding window rate limiter. Returns True if allowed, False if limit exceeded.
    """
    now = time.time()
    records = _rate_limit_records.get(key, [])
    # Filter out entries outside the window
    records = [t for t in records if now - t < window_seconds]
    if len(records) >= limit:
        _rate_limit_records[key] = records
        return False
    records.append(now)
    _rate_limit_records[key] = records
    return True


def extract_zip_safely(zip_path: str, target_dir: str) -> None:
    """
    Extracts a zip file safely, rejecting symlinks, decompression bombs, and directory traversal.
    Handles cross-platform archives, Windows backslashes, and ignores macOS resource forks.
    """
    total_size = 0
    total_files = 0
    abs_target_dir = os.path.abspath(target_dir)
    real_target_dir = os.path.realpath(abs_target_dir)

    os.makedirs(abs_target_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            total_files += 1
            total_size += member.file_size

            if total_files > MAX_ZIP_FILE_COUNT:
                raise ValueError(
                    f"Security error: ZIP contains too many files (limit: {MAX_ZIP_FILE_COUNT})."
                )
            if total_size > MAX_ZIP_EXTRACT_SIZE:
                raise ValueError(
                    f"Security error: ZIP uncompressed size exceeds {MAX_ZIP_EXTRACT_SIZE // (1024 * 1024)}MB."
                )

            # Skip OS junk like __MACOSX resource forks and .DS_Store
            clean_name = member.filename.replace("\\", "/")
            if (
                clean_name.startswith("__MACOSX/")
                or "/__MACOSX/" in clean_name
                or os.path.basename(clean_name).startswith("._")
                or clean_name.endswith(".DS_Store")
            ):
                continue

            # Check for symlink attribute only on Unix-created zip entries (create_system == 3)
            if getattr(member, "create_system", 0) == 3:
                mode = (member.external_attr >> 16) & 0xFFFF
                if mode != 0 and stat.S_ISLNK(mode):
                    raise ValueError(
                        f"Security error: ZIP entry '{member.filename}' is a forbidden symlink."
                    )

            # Check for directory traversal attempts
            clean_rel = os.path.normpath(clean_name).lstrip("/\\")
            if (
                clean_rel.startswith("..")
                or clean_rel.startswith("/")
                or clean_rel.startswith("\\")
            ):
                raise ValueError(
                    f"Security error: ZIP entry '{member.filename}' attempts path traversal."
                )

            dest_path = os.path.abspath(os.path.join(abs_target_dir, clean_rel))
            # Verify destination path stays inside target_dir
            if not (
                dest_path == abs_target_dir
                or dest_path.startswith(abs_target_dir + os.sep)
                or os.path.realpath(dest_path).startswith(real_target_dir + os.sep)
            ):
                raise ValueError(
                    f"Security error: ZIP entry '{member.filename}' attempts path traversal."
                )

            if member.is_dir() or clean_name.endswith("/"):
                os.makedirs(dest_path, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with zip_ref.open(member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


import logging

SENSITIVE_PATTERNS = [
    (r"(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED]"),
    (r"(token=)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED]"),
    (r"(secret=)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED]"),
    (r"(AIC_TOKEN_SECRET=)[^\s]+", r"\1[REDACTED]"),
    (r"(JWT_SECRET=)[^\s]+", r"\1[REDACTED]"),
    (r"(SECRET_KEY=)[^\s]+", r"\1[REDACTED]"),
    (r"(api_key=)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED]"),
]


def redact_sensitive_data(text: Any) -> Any:
    """
    Sanitizes log messages, exception details, and string inputs to prevent sensitive tokens,
    keys, and authorization headers from appearing in logs or error traces.
    """
    if not isinstance(text, str) or not text:
        return text

    sanitized = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

    # Redact actual configured secrets if app context is active
    try:
        if current_app:
            for key_name in (
                "SECRET_KEY",
                "JWT_SECRET",
                "AIC_TOKEN_SECRET",
                "GOOGLE_CLIENT_SECRET",
                "SMTP_PASSWORD",
            ):
                secret_val = current_app.config.get(key_name)
                if (
                    secret_val
                    and isinstance(secret_val, str)
                    and len(secret_val) >= 8
                    and not secret_val.startswith("default-")
                ):
                    sanitized = sanitized.replace(secret_val, "[REDACTED]")
    except Exception:
        pass

    return sanitized


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that automatically redacts sensitive security tokens, authorization headers, and secret keys.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_data(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_data(arg) if isinstance(arg, str) else arg for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive_data(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True
