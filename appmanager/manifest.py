"""
Single source of truth for reading a sub-app manifest.

Both the install pipeline and the admin registry need the same answer to
"what does this app declare?". They used to answer it differently:

* ``admin/app_installer.parse_manifest`` read ``manifest.json`` and fell back to
  discovering a Python-defined ``AppManifest``.
* ``admin/registry.parse_manifest`` read ``manifest.json`` only and returned
  ``{}``.

The result was a drift class: a Python-defined manifest worked at install time
and silently vanished when the admin page rendered, and callers had to know
which of two functions (one returning ``None``, one returning ``{}``) they had.

This module is the only place that reads a manifest. It returns a plain dict,
always (``{}`` when nothing is declared), and never raises.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("appmanager.manifest")

# Files searched for a Python-defined AppManifest, in priority order.
PYTHON_ENTRYPOINT_CANDIDATES = ("app.py", "wsgi.py", "main.py", "run.py")


def read_manifest_json(app_dir: str) -> Optional[Dict[str, Any]]:
    """
    Read ``manifest.json`` directly. Returns the parsed dict, or None when the
    file is absent, unreadable, or not a JSON object.
    """
    manifest_path = os.path.join(app_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Failed to parse manifest.json at %s: %s", app_dir, e)
        return None
    if not isinstance(data, dict):
        logger.warning("manifest.json at %s is not a JSON object", app_dir)
        return None
    return data


def discover_python_manifest(app_dir: str) -> Optional[Dict[str, Any]]:
    """
    Discover a Python-defined ``AppManifest`` from an app's entrypoint files.

    SECURITY: this executes the target module. It is only appropriate for code
    already installed on the host (or staged for review); never call it on
    untrusted input.
    """
    try:
        from appmanager_sdk.generator import load_manifest_from_module
    except Exception:
        return None

    for cand in PYTHON_ENTRYPOINT_CANDIDATES:
        cand_path = os.path.join(app_dir, cand)
        if not os.path.exists(cand_path):
            continue
        try:
            py_manifest, _ = load_manifest_from_module(cand_path, base_dir=app_dir)
            if py_manifest:
                return py_manifest.to_dict()
        except Exception as e:
            logger.warning("Python manifest discovery failed at %s: %s", cand_path, e)
    return None


def load_manifest(app_dir: str, use_python_discovery: bool = True) -> Dict[str, Any]:
    """
    Load the effective manifest for an installed/staged app directory.

    Args:
        app_dir: The app's on-disk directory.
        use_python_discovery: When True (default), fall back to executing a
            Python-defined ``AppManifest`` if no ``manifest.json`` exists. Pass
            False for callers that must not execute app code.

    Returns:
        A dict. Empty when the app declares nothing, or when the directory is
        missing.
    """
    if not app_dir or not os.path.isdir(app_dir):
        return {}

    data = read_manifest_json(app_dir)
    if data is not None:
        return data

    if use_python_discovery:
        data = discover_python_manifest(app_dir)
        if data is not None:
            return data

    return {}


__all__ = [
    "load_manifest",
    "read_manifest_json",
    "discover_python_manifest",
    "PYTHON_ENTRYPOINT_CANDIDATES",
]
