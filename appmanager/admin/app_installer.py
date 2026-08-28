import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from werkzeug.utils import secure_filename

from appmanager.database import db
from appmanager.models import InstalledApp, User, UserAppPermission
from appmanager.security import (
    extract_zip_safely,
    is_safe_repo_url,
    validate_entrypoint_path,
)
from appmanager.security_scanner import SecurityScanReport, run_security_scan
from appmanager.signals import subapp_installed, subapp_uninstalled


def sanitize_slug(slug_text: str) -> str:
    clean = secure_filename(slug_text.lower().replace(" ", "-"))
    return clean or "sub-app"


def parse_manifest(app_dir: str) -> Optional[Dict[str, Any]]:
    """
    Parses manifest.json from app_dir if present.
    If absent, attempts to discover a Python-defined AppManifest from entrypoints.
    Returns dict or None.
    """
    manifest_path = os.path.join(app_dir, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[INSTALLER WARNING] Failed to parse manifest.json at {app_dir}: {e}")
            return None

    # Fallback to Python-defined AppManifest discovery
    try:
        from appmanager_sdk.generator import load_manifest_from_module

        for cand in ["app.py", "wsgi.py", "main.py", "run.py"]:
            cand_path = os.path.join(app_dir, cand)
            if os.path.exists(cand_path):
                py_manifest, _ = load_manifest_from_module(cand_path, base_dir=app_dir)
                if py_manifest:
                    return py_manifest.to_dict()
    except Exception as e:
        print(f"[INSTALLER WARNING] Python manifest auto-discovery error at {app_dir}: {e}")

    return None


def normalize_and_flatten_app_dir(target_dir: str) -> None:
    """
    Cleans OS metadata (__MACOSX, .DS_Store, Thumbs.db) and flattens single nested
    subdirectories so manifest.json and entrypoint scripts reside at the root of target_dir.
    """
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        return

    # 1. Purge OS junk
    for junk in ["__MACOSX", ".DS_Store", "Thumbs.db", "__pycache__"]:
        junk_path = os.path.join(target_dir, junk)
        if os.path.exists(junk_path):
            if os.path.isdir(junk_path):
                shutil.rmtree(junk_path, ignore_errors=True)
            else:
                try:
                    os.remove(junk_path)
                except Exception:
                    pass

    # 2. Check if root already contains manifest.json or standard entrypoints
    has_root_manifest = os.path.exists(os.path.join(target_dir, "manifest.json"))
    has_root_entrypoint = any(
        os.path.exists(os.path.join(target_dir, cand))
        for cand in ["app.py", "wsgi.py", "main.py", "run.py"]
    )

    if has_root_manifest or has_root_entrypoint:
        return

    # 3. Locate nested directory containing app code
    entries = [e for e in os.listdir(target_dir) if not e.startswith(".")]
    candidate_dirs = [e for e in entries if os.path.isdir(os.path.join(target_dir, e))]

    nested_dir = None
    if len(candidate_dirs) == 1:
        nested_dir = os.path.join(target_dir, candidate_dirs[0])
    else:
        for c in candidate_dirs:
            cdir = os.path.join(target_dir, c)
            if os.path.exists(os.path.join(cdir, "manifest.json")) or any(
                os.path.exists(os.path.join(cdir, cand))
                for cand in ["app.py", "wsgi.py", "main.py", "run.py"]
            ):
                nested_dir = cdir
                break

    if nested_dir and os.path.isdir(nested_dir):
        for item in os.listdir(nested_dir):
            src = os.path.join(nested_dir, item)
            dst = os.path.join(target_dir, item)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    try:
                        os.remove(dst)
                    except Exception:
                        pass
            shutil.move(src, dst)
        shutil.rmtree(nested_dir, ignore_errors=True)


def validate_subapp_package(path_or_zip: str) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Validates a sub-app package or directory against AppManager specifications.
    Returns (is_valid, error_list, manifest_data).
    """
    errors: List[str] = []
    manifest: Dict[str, Any] = {}

    temp_check_dir = None

    if os.path.isfile(path_or_zip) and (
        path_or_zip.endswith(".zip") or zipfile.is_zipfile(path_or_zip)
    ):
        import tempfile

        temp_check_dir = tempfile.mkdtemp(prefix="appmanager_val_")
        try:
            extract_zip_safely(path_or_zip, temp_check_dir)
            target_dir = temp_check_dir
            normalize_and_flatten_app_dir(target_dir)
        except Exception as e:
            errors.append(f"Failed to read ZIP archive: {str(e)}")
            if temp_check_dir and os.path.exists(temp_check_dir):
                shutil.rmtree(temp_check_dir, ignore_errors=True)
            return False, errors, manifest
    else:
        target_dir = path_or_zip

    if not os.path.exists(target_dir):
        errors.append(f"Directory or file does not exist: {path_or_zip}")
        return False, errors, manifest

    # Check manifest.json or Python AppManifest
    manifest_file = os.path.join(target_dir, "manifest.json")
    if os.path.exists(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            if not isinstance(manifest, dict):
                errors.append("'manifest.json' must contain a valid JSON object.")
            else:
                if "name" not in manifest or not manifest["name"]:
                    errors.append("Manifest 'name' field is required.")
                if "slug" not in manifest or not manifest["slug"]:
                    errors.append("Manifest 'slug' field is required.")
        except Exception as e:
            errors.append(f"Invalid JSON syntax in 'manifest.json': {str(e)}")
    else:
        # Check if python-defined manifest exists
        py_manifest = parse_manifest(target_dir)
        if py_manifest:
            manifest = py_manifest
            if "name" not in manifest or not manifest["name"]:
                errors.append("Manifest 'name' field is required.")
            if "slug" not in manifest or not manifest["slug"]:
                errors.append("Manifest 'slug' field is required.")
        else:
            errors.append("Missing required 'manifest.json' file or Python-defined AppManifest.")

    # Check entry point file
    entry_point = manifest.get("entry_point") if manifest else discover_entrypoint(target_dir)
    is_safe_ep, ep_result = validate_entrypoint_path(target_dir, entry_point)
    if not is_safe_ep:
        errors.append(f"Security error: {ep_result}")
    else:
        entry_file = ep_result
        if not os.path.exists(entry_file):
            errors.append(f"Entrypoint module was not found at {entry_file}.")
        else:
            try:
                with open(entry_file, "r", encoding="utf-8") as ef:
                    ast.parse(ef.read(), filename=entry_file)
            except SyntaxError as syn_err:
                errors.append(f"Syntax error in entrypoint file: {syn_err}")
            except Exception as read_err:
                errors.append(f"Could not read entrypoint file: {read_err}")

    # Cleanup temp directory if created
    if temp_check_dir and os.path.exists(temp_check_dir):
        shutil.rmtree(temp_check_dir, ignore_errors=True)

    is_valid = len(errors) == 0
    return is_valid, errors, manifest


def discover_entrypoint(app_dir: str) -> str:
    """
    Search for common Flask entrypoint files (app.py, wsgi.py, main.py, run.py) or manifest.json.
    Returns 'module_name:app_variable' string e.g. 'app:app'
    """
    manifest = parse_manifest(app_dir)
    if manifest and "entry_point" in manifest:
        return manifest["entry_point"]

    candidates = ["app.py", "wsgi.py", "main.py", "run.py"]
    for candidate in candidates:
        if os.path.exists(os.path.join(app_dir, candidate)):
            module_name = candidate[:-3]  # Strip .py
            return f"{module_name}:app"

    # Default fallback
    return "app:app"


def install_dependencies(app_dir: str) -> None:
    req_file = os.path.join(app_dir, "requirements.txt")
    if os.path.exists(req_file):
        try:
            print(f"[INSTALLER] Installing dependencies for sub-app at {app_dir}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
            print("[INSTALLER] Sub-app dependencies installed successfully.")
        except Exception as e:
            print(f"[INSTALLER WARNING] Failed to install requirements.txt: {e}")


def load_wsgi_app_from_path(app_dir: str, entry_point: str = "app:app") -> Any:
    """
    Dynamically imports a Flask/WSGI app instance from a specified directory and entrypoint.
    Uses scoped module namespaces (appmanager.installed.<slug>) without sys.path pollution.
    """
    if entry_point.count(":") != 1:
        entry_point = "app:app"

    module_name, app_var_name = entry_point.split(":")
    is_safe, module_path_or_err = validate_entrypoint_path(app_dir, module_name)
    if not is_safe:
        raise ValueError(f"Security error: {module_path_or_err}")

    module_path = module_path_or_err
    if not os.path.exists(module_path):
        raise FileNotFoundError(f"Entrypoint file '{module_name}.py' not found in {app_dir}")

    # Unique scoped module spec name to avoid sys.path pollution
    slug = os.path.basename(app_dir)
    scoped_module_name = f"appmanager.installed.{slug}.{module_name}"

    spec = importlib.util.spec_from_file_location(scoped_module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load spec for {module_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[scoped_module_name] = mod
    spec.loader.exec_module(mod)

    # Check for Flask app instance or callable create_app factory
    if hasattr(mod, app_var_name):
        app_obj = getattr(mod, app_var_name)
        if callable(app_obj) and not hasattr(app_obj, "wsgi_app"):
            # Factory function e.g. create_app()
            app_instance = app_obj()
        else:
            app_instance = app_obj
        return app_instance

    raise AttributeError(f"Module '{module_name}' in {app_dir} does not export '{app_var_name}'")


# In-memory staging storage: staging_id -> dict of staged info
_staged_installations: Dict[str, Dict[str, Any]] = {}
STAGING_EXPIRY_SECONDS = 1800  # 30 minutes


def _write_staging_metadata(staging_dir: str, meta: Dict[str, Any]) -> None:
    """Saves staging session metadata to disk inside the sandboxed staging directory."""
    meta_path = os.path.join(staging_dir, "_staging_meta.json")
    try:
        # Don't serialize scan_report object directly to JSON if it's an object
        serializable = dict(meta)
        if "scan_report" in serializable and hasattr(serializable["scan_report"], "to_dict"):
            serializable["scan_report"] = serializable["scan_report"].to_dict()
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f)
    except Exception as e:
        print(f"[INSTALLER WARNING] Could not write staging metadata to disk: {e}")


def cleanup_expired_staged_sessions(max_age: int = STAGING_EXPIRY_SECONDS) -> None:
    now = time.time()
    # 1. Clean in-memory
    expired_ids = [
        sid
        for sid, data in list(_staged_installations.items())
        if now - data.get("created_at", 0) > max_age
    ]
    for sid in expired_ids:
        cancel_staged_app(sid)

    # 2. Clean orphaned disk directories
    try:
        import tempfile

        temp_dir = tempfile.gettempdir()
        for entry in os.listdir(temp_dir):
            if entry.startswith("appmanager_stage_"):
                entry_path = os.path.join(temp_dir, entry)
                if os.path.isdir(entry_path):
                    mtime = os.path.getmtime(entry_path)
                    if now - mtime > max_age:
                        shutil.rmtree(entry_path, ignore_errors=True)
    except Exception:
        pass


def get_staged_session(staging_id: str) -> Optional[Dict[str, Any]]:
    if staging_id in _staged_installations:
        data = _staged_installations[staging_id]
        if time.time() - data.get("created_at", 0) <= STAGING_EXPIRY_SECONDS:
            return data

    # Disk fallback: look for staging directory on disk if server restarted
    import tempfile

    temp_dir = tempfile.gettempdir()
    for entry in os.listdir(temp_dir):
        if entry.startswith(f"appmanager_{staging_id}_") or entry == f"appmanager_{staging_id}":
            entry_path = os.path.join(temp_dir, entry)
            meta_path = os.path.join(entry_path, "_staging_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                    meta_data["staging_dir"] = entry_path
                    if time.time() - meta_data.get("created_at", 0) <= STAGING_EXPIRY_SECONDS:
                        _staged_installations[staging_id] = meta_data
                        return meta_data
                except Exception:
                    pass
    return None


def stage_git_repo(
    repo_url: str,
    name: str,
    slug: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> Tuple[str, SecurityScanReport, Dict[str, Any]]:
    """
    Clones a Git repository into a sandboxed staging directory, executes a static
    security pre-check audit, and stores the staged session.
    """
    if not is_safe_repo_url(repo_url):
        raise ValueError(f"Invalid or unsafe git repository URL: '{repo_url}'")

    cleanup_expired_staged_sessions()

    import tempfile

    staging_id = f"stage_{uuid.uuid4().hex[:12]}"
    temp_stage_dir = tempfile.mkdtemp(prefix=f"appmanager_{staging_id}_")

    try:
        try:
            import git

            git.Repo.clone_from(repo_url, temp_stage_dir, depth=1)
        except Exception:
            # Pass '--' separator before repo_url to prevent git command flag injection
            res = subprocess.run(
                ["git", "clone", "--depth", "1", "--", repo_url, temp_stage_dir],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    res.stderr.strip() or f"Failed to clone repository from '{repo_url}'."
                )
    except Exception as git_err:
        if os.path.exists(temp_stage_dir):
            shutil.rmtree(temp_stage_dir, ignore_errors=True)
        raise ValueError(f"Failed to clone Git repository: {git_err}")

    normalize_and_flatten_app_dir(temp_stage_dir)

    # Validate that repository contains valid app files
    manifest = parse_manifest(temp_stage_dir) or {}
    resolved_entry_point = (
        entry_point or manifest.get("entry_point") or discover_entrypoint(temp_stage_dir)
    )
    if not manifest and not any(
        os.path.exists(os.path.join(temp_stage_dir, cand))
        for cand in ["app.py", "wsgi.py", "main.py", "run.py"]
    ):
        if os.path.exists(temp_stage_dir):
            shutil.rmtree(temp_stage_dir, ignore_errors=True)
        raise ValueError(
            "Invalid sub-app repository: No valid entrypoint (app.py, wsgi.py) or manifest.json found in repository."
        )

    resolved_name = manifest.get("name") or name
    resolved_slug = sanitize_slug(slug or manifest.get("slug") or resolved_name)

    # Run static security pre-check
    scan_report = run_security_scan(temp_stage_dir)

    _staged_installations[staging_id] = {
        "staging_id": staging_id,
        "staging_dir": temp_stage_dir,
        "source_type": "git",
        "source_url": repo_url,
        "name": resolved_name,
        "slug": resolved_slug,
        "entry_point": resolved_entry_point,
        "manifest": manifest,
        "scan_report": scan_report,
        "created_at": time.time(),
    }
    _write_staging_metadata(temp_stage_dir, _staged_installations[staging_id])

    return staging_id, scan_report, manifest


def stage_zip_file(
    zip_file_storage: Any,
    name: str,
    slug: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> Tuple[str, SecurityScanReport, Dict[str, Any]]:
    """
    Safely extracts an uploaded zip archive into a sandboxed staging directory, executes
    a static security pre-check audit, and stores the staged session.
    """
    cleanup_expired_staged_sessions()

    import tempfile

    staging_id = f"stage_{uuid.uuid4().hex[:12]}"
    temp_stage_dir = tempfile.mkdtemp(prefix=f"appmanager_{staging_id}_")

    resolved_slug = sanitize_slug(slug or name)
    filename = getattr(zip_file_storage, "filename", "upload.zip")
    temp_zip_path = os.path.join(
        current_app.config.get("TEMP_UPLOAD_DIR", tempfile.gettempdir()),
        f"{resolved_slug}_{staging_id}_{secure_filename(filename)}",
    )
    os.makedirs(os.path.dirname(temp_zip_path), exist_ok=True)
    zip_file_storage.save(temp_zip_path)

    try:
        extract_zip_safely(temp_zip_path, temp_stage_dir)
        normalize_and_flatten_app_dir(temp_stage_dir)
    except Exception as extract_err:
        if os.path.exists(temp_stage_dir):
            shutil.rmtree(temp_stage_dir, ignore_errors=True)
        if isinstance(extract_err, zipfile.BadZipFile):
            raise ValueError(f"Invalid or corrupted ZIP archive: {extract_err}")
        raise ValueError(f"Failed to extract ZIP archive: {extract_err}")
    finally:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)

    # Validate that archive contains valid app files
    manifest = parse_manifest(temp_stage_dir) or {}
    resolved_entry_point = (
        entry_point or manifest.get("entry_point") or discover_entrypoint(temp_stage_dir)
    )
    if not manifest and not any(
        os.path.exists(os.path.join(temp_stage_dir, cand))
        for cand in ["app.py", "wsgi.py", "main.py", "run.py"]
    ):
        if os.path.exists(temp_stage_dir):
            shutil.rmtree(temp_stage_dir, ignore_errors=True)
        raise ValueError(
            "Invalid sub-app package: No valid entrypoint (app.py, wsgi.py) or manifest.json found in archive."
        )

    resolved_name = manifest.get("name") or name
    resolved_slug = sanitize_slug(slug or manifest.get("slug") or resolved_name)

    # Run static security pre-check
    scan_report = run_security_scan(temp_stage_dir)

    _staged_installations[staging_id] = {
        "staging_id": staging_id,
        "staging_dir": temp_stage_dir,
        "source_type": "zip",
        "source_url": getattr(zip_file_storage, "filename", "archive.zip"),
        "name": resolved_name,
        "slug": resolved_slug,
        "entry_point": resolved_entry_point,
        "manifest": manifest,
        "scan_report": scan_report,
        "created_at": time.time(),
    }
    _write_staging_metadata(temp_stage_dir, _staged_installations[staging_id])

    return staging_id, scan_report, manifest


def finalize_staged_installation(
    staging_id: str,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> InstalledApp:
    """
    Finalizes installation of a staged application: moves files into INSTALLED_APPS_DIR,
    installs requirements, registers in database, configures settings and permissions.
    """
    session = get_staged_session(staging_id)
    if not session:
        raise ValueError(
            f"Staged session '{staging_id}' not found or has expired. Please run precheck again."
        )

    _staged_installations.pop(staging_id, None)

    staging_dir = session["staging_dir"]
    if not os.path.exists(staging_dir):
        raise FileNotFoundError(f"Staging directory for '{staging_id}' no longer exists.")

    final_slug = sanitize_slug(slug or session.get("slug") or session.get("name") or "sub-app")
    target_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], final_slug)

    if os.path.exists(target_dir):
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise ValueError(f"An app with slug '{final_slug}' is already installed.")

    # Remove staging metadata file before moving into installed_apps
    meta_path = os.path.join(staging_dir, "_staging_meta.json")
    if os.path.exists(meta_path):
        try:
            os.remove(meta_path)
        except Exception:
            pass

    # Move staged directory to final location
    try:
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        shutil.move(staging_dir, target_dir)
    except Exception as move_err:
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to move staged app to target destination: {move_err}")

    manifest = parse_manifest(target_dir) or session.get("manifest") or {}
    final_name = name or session.get("name") or manifest.get("name") or final_slug
    description = manifest.get("description") or session.get("manifest", {}).get("description")
    final_entry_point = (
        entry_point
        or session.get("entry_point")
        or manifest.get("entry_point")
        or discover_entrypoint(target_dir)
    )
    app_type = manifest.get("app_type", "standalone")
    target_app = manifest.get("target_app")
    default_has_ui = False if app_type == "extension" else True
    raw_has_ui = manifest.get("has_web_ui", manifest.get("has_ui", default_has_ui))
    if isinstance(raw_has_ui, str):
        has_web_ui = raw_has_ui.lower() in ("true", "1", "yes")
    else:
        has_web_ui = bool(raw_has_ui)

    # Extract default settings if declared in manifest
    initial_settings = {}
    if "settings" in manifest and isinstance(manifest["settings"], dict):
        for s_key, s_val in manifest["settings"].items():
            if isinstance(s_val, dict):
                initial_settings[s_key] = s_val.get("default", "")
            else:
                initial_settings[s_key] = s_val

    install_dependencies(target_dir)

    installed_app = InstalledApp(
        name=final_name,
        slug=final_slug,
        description=description,
        source_type=session.get("source_type", "upload"),
        source_url=session.get("source_url", ""),
        entry_point=final_entry_point,
        app_type=app_type,
        target_app=target_app,
        has_web_ui=has_web_ui,
        is_active=True,
    )
    if initial_settings:
        installed_app.set_settings(initial_settings)

    db.session.add(installed_app)
    db.session.commit()

    users = User.query.all()
    for u in users:
        perm = UserAppPermission(user_id=u.id, app_id=installed_app.id, can_access=True)
        db.session.add(perm)
    db.session.commit()

    # Sync declared admin panels from the manifest
    try:
        from appmanager.admin.registry import sync_panels

        sync_panels(installed_app, manifest)
    except Exception:
        pass

    try:
        subapp_installed.send(
            None,
            app_slug=final_slug,
            source_type=session.get("source_type"),
            app_id=installed_app.id,
        )
    except Exception:
        pass

    try:
        from appmanager.hooks import trigger_hook

        trigger_hook(
            "on_app_installed", installed_app=installed_app, source_type=session.get("source_type")
        )
    except Exception:
        pass

    return installed_app


def cancel_staged_app(staging_id: str) -> bool:
    """
    Cancels a staged application and cleans up temporary files.
    """
    session = _staged_installations.pop(staging_id, None)
    if session and os.path.exists(session.get("staging_dir", "")):
        shutil.rmtree(session["staging_dir"], ignore_errors=True)
        return True
    return False


def install_from_git(
    repo_url: str,
    name: str,
    slug: Optional[str] = None,
    description: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> InstalledApp:
    staging_id, scan_report, manifest = stage_git_repo(
        repo_url=repo_url, name=name, slug=slug, entry_point=entry_point
    )
    return finalize_staged_installation(
        staging_id=staging_id, name=name, slug=slug, entry_point=entry_point
    )


def install_from_zip(
    zip_file_storage: Any,
    name: str,
    slug: Optional[str] = None,
    description: Optional[str] = None,
    entry_point: Optional[str] = None,
) -> InstalledApp:
    staging_id, scan_report, manifest = stage_zip_file(
        zip_file_storage=zip_file_storage, name=name, slug=slug, entry_point=entry_point
    )
    return finalize_staged_installation(
        staging_id=staging_id, name=name, slug=slug, entry_point=entry_point
    )


def export_app_to_zip(slug: str, output_path: Optional[str] = None) -> str:
    """
    Packages an installed sub-app directory into a clean distributable ZIP file.
    """
    app_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], slug)
    if not os.path.exists(app_dir):
        raise FileNotFoundError(f"Sub-app directory for '{slug}' not found.")

    if not output_path:
        output_path = f"{slug}.zip"

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(app_dir):
            # Ignore __pycache__, .git, and build artifacts
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".pytest_cache", "venv")]
            for file in files:
                if file.endswith((".pyc", ".pyo", ".DS_Store")):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, app_dir)
                zipf.write(file_path, arcname)

    return output_path


def uninstall_app(app_id: int) -> Tuple[bool, str]:
    app_record = db.session.get(InstalledApp, app_id)
    if not app_record:
        return False, "App not found."

    slug = app_record.slug
    target_dir = os.path.join(current_app.config["INSTALLED_APPS_DIR"], slug)
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir, ignore_errors=True)

    # Invalidate cached WSGI instance if present in extension
    if hasattr(current_app, "extensions") and "appmanager" in current_app.extensions:
        current_app.extensions["appmanager"].clear_cache(slug=slug)

    db.session.delete(app_record)
    db.session.commit()

    try:
        subapp_uninstalled.send(None, app_slug=slug, app_id=app_id)
    except Exception:
        pass

    try:
        from appmanager.hooks import trigger_hook

        trigger_hook("on_app_uninstalled", app_id=app_id, app_slug=slug)
    except Exception:
        pass

    return True, "App uninstalled successfully."
