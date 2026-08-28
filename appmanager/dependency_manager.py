import importlib.metadata
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except ImportError:  # pragma: no cover
    # Basic fallback if packaging is not directly installed
    Requirement = None  # type: ignore
    SpecifierSet = None  # type: ignore
    Version = None  # type: ignore

CORE_HOST_PACKAGES = {
    "flask": "Flask core web framework",
    "werkzeug": "WSGI utility library",
    "sqlalchemy": "SQL database toolkit",
    "flask-sqlalchemy": "Flask SQLAlchemy integration",
    "jinja2": "Template rendering engine",
    "pyjwt": "JSON Web Token library",
    "requests": "HTTP client library",
}


@dataclass
class DependencyItem:
    name: str
    specifier: str
    installed_version: Optional[str] = None
    is_installed: bool = False
    is_compatible: bool = True
    status: str = "unknown"  # 'satisfied', 'to_install', 'conflict', 'core_override'
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DependencyAnalysisReport:
    is_safe: bool = True
    python_version_ok: bool = True
    python_version_required: Optional[str] = None
    python_version_current: str = field(default_factory=platform.python_version)
    venv_mode: str = "singular"
    items: List[DependencyItem] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    to_install: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "python_version_ok": self.python_version_ok,
            "python_version_required": self.python_version_required,
            "python_version_current": self.python_version_current,
            "venv_mode": self.venv_mode,
            "items": [item.to_dict() for item in self.items],
            "conflicts": self.conflicts,
            "warnings": self.warnings,
            "to_install": self.to_install,
        }


def check_python_version_compatibility(
    required_spec: Optional[str],
) -> Tuple[bool, str]:
    """
    Checks if the current runtime Python version satisfies the given specifier (e.g. '>=3.10', '>=3.9,<3.13').
    """
    current_ver_str = platform.python_version()
    if not required_spec or required_spec.strip() in ("", "*"):
        return True, f"Python {current_ver_str} (Any version acceptable)"

    cleaned_spec = required_spec.strip()
    # Normalize common non-standard formats e.g. "3.10" or "Python 3.10" -> ">=3.10"
    if cleaned_spec.lower().startswith("python"):
        cleaned_spec = cleaned_spec[6:].strip()
    if not any(cleaned_spec.startswith(op) for op in ("==", ">=", "<=", ">", "<", "~=", "!=")):
        cleaned_spec = f">={cleaned_spec}"

    try:
        if SpecifierSet and Version:
            spec = SpecifierSet(cleaned_spec)
            current_ver = Version(current_ver_str)
            is_ok = current_ver in spec
            return (
                is_ok,
                f"Current: Python {current_ver_str}, Required: {cleaned_spec}"
                if is_ok
                else f"Python version mismatch! Current {current_ver_str} does not satisfy {cleaned_spec}",
            )
    except Exception:
        pass

    # Simple fallback check for >=X.Y format
    m = re.search(r"(\d+)\.(\d+)", cleaned_spec)
    if m:
        req_major, req_minor = int(m.group(1)), int(m.group(2))
        curr_major, curr_minor = sys.version_info.major, sys.version_info.minor
        is_ok = (curr_major > req_major) or (curr_major == req_major and curr_minor >= req_minor)
        return (
            is_ok,
            f"Current: Python {curr_major}.{curr_minor}, Required: {cleaned_spec}"
            if is_ok
            else f"Python version mismatch: required {cleaned_spec}, running {curr_major}.{curr_minor}",
        )

    return True, f"Python {current_ver_str}"


def get_installed_distribution_version(
    package_name: str, custom_env_site_packages: Optional[str] = None
) -> Optional[str]:
    """
    Returns the currently installed version of a package name, or None.
    """
    norm_name = package_name.lower().replace("_", "-")
    try:
        return importlib.metadata.version(norm_name)
    except importlib.metadata.PackageNotFoundError:
        pass

    # Secondary check across all distributions
    try:
        for dist in importlib.metadata.distributions():
            dist_name = (
                dist.metadata.get("Name", dist.name if hasattr(dist, "name") else "")
                .lower()
                .replace("_", "-")
            )
            if dist_name == norm_name:
                return dist.version
    except Exception:
        pass

    return None


def parse_requirements_txt(content: str) -> List[Tuple[str, str]]:
    """
    Parses lines from a requirements.txt file and returns [(pkg_name, specifier_str), ...].
    """
    results: List[Tuple[str, str]] = []
    lines = content.splitlines()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", "-r", "--", "-f", "-i")):
            continue
        # Strip inline comments
        if " #" in line:
            line = line.split(" #")[0].strip()

        try:
            if Requirement:
                req = Requirement(line)
                results.append((req.name, str(req.specifier) if req.specifier else ""))
                continue
        except Exception:
            pass

        # Regex fallback for PEP 508 strings: e.g. "Flask>=2.0.0,<3.0" -> name="Flask", spec=">=2.0.0,<3.0"
        m = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*(.*)$", line)
        if m:
            pkg_name = m.group(1)
            spec_part = m.group(2).strip()
            results.append((pkg_name, spec_part))

    return results


def analyze_dependencies(
    app_dir: str,
    manifest: Optional[Dict[str, Any]] = None,
    venv_mode: str = "singular",
    installed_apps_dir: Optional[str] = None,
) -> DependencyAnalysisReport:
    """
    Comprehensive pre-installation/pre-update dependency analyzer.
    Checks Python version compatibility and inspects package requirements for conflicts.
    """
    report = DependencyAnalysisReport(venv_mode=venv_mode)

    # 1. Python version compatibility
    py_req = None
    if manifest:
        py_req = manifest.get("python_version") or manifest.get("requires_python")
    report.python_version_required = py_req

    is_py_ok, py_msg = check_python_version_compatibility(py_req)
    report.python_version_ok = is_py_ok
    if not is_py_ok:
        report.is_safe = False
        report.conflicts.append(f"Python Version Incompatibility: {py_msg}")

    # 2. Check requirements.txt
    req_file = os.path.join(app_dir, "requirements.txt")
    if not os.path.exists(req_file):
        return report

    try:
        with open(req_file, "r", encoding="utf-8") as f:
            req_content = f.read()
    except Exception as e:
        report.warnings.append(f"Could not read requirements.txt: {e}")
        return report

    parsed_reqs = parse_requirements_txt(req_content)

    for pkg_name, spec_str in parsed_reqs:
        norm_name = pkg_name.lower().replace("_", "-")
        installed_ver = get_installed_distribution_version(norm_name)
        is_installed = installed_ver is not None

        item = DependencyItem(
            name=pkg_name,
            specifier=spec_str or "any",
            installed_version=installed_ver,
            is_installed=is_installed,
        )

        if not is_installed:
            item.status = "to_install"
            item.message = f"Will be installed ({spec_str or 'latest'})"
            report.to_install.append(f"{pkg_name}{spec_str}")
        else:
            # Check compatibility against installed version
            is_compat = True
            if spec_str and SpecifierSet and Version:
                try:
                    spec = SpecifierSet(spec_str)
                    is_compat = Version(installed_ver) in spec
                except Exception:
                    is_compat = True

            item.is_compatible = is_compat

            if is_compat:
                item.status = "satisfied"
                item.message = f"Satisfied (installed: v{installed_ver})"
            else:
                # Incompatibility detected with active environment
                item.status = "conflict"
                item.message = f"Conflict: installed v{installed_ver} does not satisfy {spec_str}"
                conflict_msg = (
                    f"Dependency Conflict for '{pkg_name}': installed v{installed_ver} in "
                    f"environment contradicts requested '{spec_str}'"
                )

                # If this is a core host package (like Flask), elevate warning to safety blocker
                if norm_name in CORE_HOST_PACKAGES:
                    conflict_msg += (
                        f" (CRITICAL: Modifying {pkg_name} may break AppManager host portal!)"
                    )
                    if venv_mode == "singular":
                        report.is_safe = False

                report.conflicts.append(conflict_msg)

        # In singular mode, check if requested package overrides host core libraries
        if venv_mode == "singular" and norm_name in CORE_HOST_PACKAGES:
            if not is_installed or (item.status == "conflict"):
                report.warnings.append(
                    f"Sub-app declares host core package '{pkg_name}'. "
                    f"Using shared host version {installed_ver}."
                )

        report.items.append(item)

    return report


def install_app_dependencies(
    app_dir: str,
    venv_mode: str = "singular",
    timeout: int = 180,
) -> Tuple[bool, str]:
    """
    Installs sub-app dependencies from requirements.txt based on configured venv mode.
    Returns (success, output_or_error_message).
    """
    req_file = os.path.join(app_dir, "requirements.txt")
    if not os.path.exists(req_file):
        return True, "No requirements.txt found (nothing to install)."

    if venv_mode == "isolated":
        venv_dir = os.path.join(app_dir, ".venv")
        pip_path = os.path.join(venv_dir, "bin", "pip")
        if not os.path.exists(pip_path):
            pip_path = os.path.join(venv_dir, "Scripts", "pip.exe")

        if not os.path.exists(pip_path):
            # Create isolated virtual environment
            try:
                subprocess.run(
                    [sys.executable, "-m", "venv", venv_dir],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except Exception as venv_err:
                return False, f"Failed to initialize isolated virtual environment: {venv_err}"

        cmd = [pip_path, "install", "--upgrade", "pip", "setuptools"]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=60)
        except Exception:
            pass

        install_cmd = [pip_path, "install", "-r", req_file]
    else:
        # Singular shared host virtual environment
        install_cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]

    try:
        proc = subprocess.run(
            install_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return True, proc.stdout or "Dependencies installed successfully."
        else:
            return False, f"Pip installation failed (code {proc.returncode}): {proc.stderr}"
    except subprocess.TimeoutExpired:
        return False, f"Dependency installation timed out after {timeout} seconds."
    except Exception as e:
        return False, f"Unexpected error during dependency installation: {str(e)}"


def analyze_uninstall_dependencies(
    slug_to_remove: str,
    installed_apps_dir: str,
) -> Dict[str, Any]:
    """
    Analyzes dependencies of an app to be uninstalled against all remaining sub-apps
    to ensure shared dependencies are preserved.
    """
    target_app_dir = os.path.join(installed_apps_dir, slug_to_remove)
    target_req_file = os.path.join(target_app_dir, "requirements.txt")

    target_packages = set()
    if os.path.exists(target_req_file):
        try:
            with open(target_req_file, "r", encoding="utf-8") as f:
                for pkg_name, _ in parse_requirements_txt(f.read()):
                    target_packages.add(pkg_name.lower().replace("_", "-"))
        except Exception:
            pass

    # Collect dependencies from all other installed sub-apps
    other_app_packages = set()
    if os.path.exists(installed_apps_dir):
        for entry in os.listdir(installed_apps_dir):
            if entry == slug_to_remove:
                continue
            other_dir = os.path.join(installed_apps_dir, entry)
            if not os.path.isdir(other_dir):
                continue
            other_req = os.path.join(other_dir, "requirements.txt")
            if os.path.exists(other_req):
                try:
                    with open(other_req, "r", encoding="utf-8") as f:
                        for pkg, _ in parse_requirements_txt(f.read()):
                            other_app_packages.add(pkg.lower().replace("_", "-"))
                except Exception:
                    pass

    # Include host core packages in protected set
    protected_packages = other_app_packages.union(
        {p.lower().replace("_", "-") for p in CORE_HOST_PACKAGES}
    )

    shared_packages = sorted(list(target_packages.intersection(protected_packages)))
    orphaned_packages = sorted(list(target_packages.difference(protected_packages)))

    return {
        "slug": slug_to_remove,
        "target_packages": sorted(list(target_packages)),
        "shared_packages_preserved": shared_packages,
        "orphaned_packages": orphaned_packages,
        "is_safe_to_uninstall": True,
        "message": (
            f"{len(shared_packages)} package(s) are shared with other sub-apps/host and will be preserved."
            if shared_packages
            else "All dependencies are isolated to this app."
        ),
    }
