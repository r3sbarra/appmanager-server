import ast
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from appmanager.security import (
    validate_entrypoint_path,
)

# Forbidden binary and script file extensions in sub-app packages
FORBIDDEN_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".sh",
    ".bash",
    ".zsh",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".jar",
    ".war",
    ".class",
}

DANGEROUS_CALLS = {
    # High/Critical code & command execution
    "eval": (
        "CRITICAL",
        "Code Injection",
        "Direct use of eval() allows arbitrary Python code execution.",
    ),
    "exec": (
        "CRITICAL",
        "Code Injection",
        "Direct use of exec() allows arbitrary dynamic code execution.",
    ),
    "compile": ("HIGH", "Code Injection", "Use of compile() dynamically compiles code strings."),
    "__import__": ("HIGH", "Dynamic Import", "Direct dynamic module import via __import__()."),
    "globals": ("MEDIUM", "Namespace Access", "Direct access to global symbol namespace."),
    "locals": ("LOW", "Namespace Access", "Access to local symbol namespace."),
    "posix.system": (
        "CRITICAL",
        "Command Injection",
        "Direct OS system execution via posix.system.",
    ),
    "pty.spawn": (
        "CRITICAL",
        "Interactive Shell / Reverse Shell",
        "pty.spawn() can spawn arbitrary interactive shells.",
    ),
    "pty.fork": ("CRITICAL", "Process Forking", "pty.fork() forks process with pseudo-terminal."),
    # Dangerous deserialization
    "pickle.loads": (
        "CRITICAL",
        "Insecure Deserialization",
        "pickle.loads() can execute arbitrary code during deserialization.",
    ),
    "pickle.load": (
        "CRITICAL",
        "Insecure Deserialization",
        "pickle.load() can execute arbitrary code during deserialization.",
    ),
    "_pickle.loads": (
        "CRITICAL",
        "Insecure Deserialization",
        "_pickle.loads() can execute arbitrary code during deserialization.",
    ),
    "_pickle.load": (
        "CRITICAL",
        "Insecure Deserialization",
        "_pickle.load() can execute arbitrary code during deserialization.",
    ),
    "marshal.loads": (
        "HIGH",
        "Insecure Deserialization",
        "marshal.loads() deserializes arbitrary Python bytecode.",
    ),
    "shelve.open": (
        "MEDIUM",
        "Insecure Deserialization",
        "shelve.open() uses pickle under the hood.",
    ),
}

DANGEROUS_MODULES = {
    "os.system": (
        "CRITICAL",
        "Command Injection",
        "os.system() executes arbitrary shell commands.",
    ),
    "os.popen": (
        "CRITICAL",
        "Command Injection",
        "os.popen() opens a pipe to/from a shell command.",
    ),
    "os.spawnl": ("CRITICAL", "Process Execution", "os.spawnl() spawns arbitrary OS processes."),
    "os.spawnle": ("CRITICAL", "Process Execution", "os.spawnle() spawns arbitrary OS processes."),
    "os.spawnlp": ("CRITICAL", "Process Execution", "os.spawnlp() spawns arbitrary OS processes."),
    "os.spawnlpe": (
        "CRITICAL",
        "Process Execution",
        "os.spawnlpe() spawns arbitrary OS processes.",
    ),
    "os.spawnv": ("CRITICAL", "Process Execution", "os.spawnv() spawns arbitrary OS processes."),
    "os.spawnve": ("CRITICAL", "Process Execution", "os.spawnve() spawns arbitrary OS processes."),
    "os.spawnvp": ("CRITICAL", "Process Execution", "os.spawnvp() spawns arbitrary OS processes."),
    "os.spawnvpe": (
        "CRITICAL",
        "Process Execution",
        "os.spawnvpe() spawns arbitrary OS processes.",
    ),
}

SUBPROCESS_FUNCS = {
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
}

SUSPICIOUS_REQUIREMENTS_FLAGS = [
    ("--extra-index-url", "HIGH", "Custom package index repository overrides default PyPI index."),
    ("--index-url", "HIGH", "Direct package index override."),
    ("--find-links", "MEDIUM", "Package links to external directory or URL."),
    ("--trusted-host", "HIGH", "Disables SSL verification for pip installations."),
    ("-e", "MEDIUM", "Editable package installation flag."),
    ("--editable", "MEDIUM", "Editable package installation flag."),
]


@dataclass
class SecurityFinding:
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'
    category: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecurityScanReport:
    is_safe: bool
    risk_level: str  # 'CLEAN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    files_scanned: int = 0
    py_files_scanned: int = 0
    findings: List[SecurityFinding] = field(default_factory=list)
    manifest_info: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "risk_level": self.risk_level,
            "files_scanned": self.files_scanned,
            "py_files_scanned": self.py_files_scanned,
            "findings": [f.to_dict() for f in self.findings],
            "manifest_info": self.manifest_info,
            "summary": self.summary,
            "counts": {
                "critical": sum(1 for f in self.findings if f.severity == "CRITICAL"),
                "high": sum(1 for f in self.findings if f.severity == "HIGH"),
                "medium": sum(1 for f in self.findings if f.severity == "MEDIUM"),
                "low": sum(1 for f in self.findings if f.severity == "LOW"),
                "info": sum(1 for f in self.findings if f.severity == "INFO"),
            },
        }


class CodeSecurityVisitor(ast.NodeVisitor):
    """
    Statically analyzes Python AST nodes to identify potentially malicious code patterns
    without executing or importing untrusted modules.
    """

    def __init__(self, filepath: str, source_lines: List[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.findings: List[SecurityFinding] = []
        self.imports: Dict[str, str] = {}  # alias -> full_name

    def _get_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.name
            asname = alias.asname or name
            self.imports[asname] = name
            if name in ("pty", "telnetlib"):
                self.findings.append(
                    SecurityFinding(
                        severity="MEDIUM",
                        category="Suspicious Module Import",
                        message=f"Importing module '{name}' which is commonly used for shell access.",
                        file=self.filepath,
                        line=node.lineno,
                        snippet=self._get_snippet(node.lineno),
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            full_name = f"{module}.{alias.name}" if module else alias.name
            asname = alias.asname or alias.name
            self.imports[asname] = full_name
            if full_name in DANGEROUS_CALLS or full_name in DANGEROUS_MODULES:
                info = DANGEROUS_CALLS.get(full_name) or DANGEROUS_MODULES.get(full_name)
                sev, cat, desc = info
                self.findings.append(
                    SecurityFinding(
                        severity=sev,
                        category=cat,
                        message=f"Import of dangerous function '{full_name}': {desc}",
                        file=self.filepath,
                        line=node.lineno,
                        snippet=self._get_snippet(node.lineno),
                    )
                )
        self.generic_visit(node)

    def _resolve_func_name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        elif isinstance(node, ast.Attribute):
            val_name = self._resolve_func_name(node.value)
            if val_name:
                resolved_base = self.imports.get(val_name, val_name)
                return f"{resolved_base}.{node.attr}"
            return node.attr
        return None

    def visit_Call(self, node: ast.Call):
        func_name = self._resolve_func_name(node.func)
        lineno = getattr(node, "lineno", 1)
        snippet = self._get_snippet(lineno)

        if func_name:
            # Check dangerous standalone or qualified calls
            if func_name in DANGEROUS_CALLS:
                sev, cat, desc = DANGEROUS_CALLS[func_name]
                self.findings.append(
                    SecurityFinding(
                        severity=sev,
                        category=cat,
                        message=desc,
                        file=self.filepath,
                        line=lineno,
                        snippet=snippet,
                    )
                )
            elif func_name in DANGEROUS_MODULES:
                sev, cat, desc = DANGEROUS_MODULES[func_name]
                self.findings.append(
                    SecurityFinding(
                        severity=sev,
                        category=cat,
                        message=desc,
                        file=self.filepath,
                        line=lineno,
                        snippet=snippet,
                    )
                )
            elif func_name in SUBPROCESS_FUNCS:
                # Inspect subprocess invocation
                shell_kw = any(
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in node.keywords
                )
                if shell_kw:
                    self.findings.append(
                        SecurityFinding(
                            severity="CRITICAL",
                            category="Command Injection Risk",
                            message=f"{func_name}() called with shell=True, which is susceptible to OS command injection.",
                            file=self.filepath,
                            line=lineno,
                            snippet=snippet,
                        )
                    )
                else:
                    self.findings.append(
                        SecurityFinding(
                            severity="MEDIUM",
                            category="Process Execution",
                            message=f"{func_name}() spawns external OS processes. Verify arguments are sanitized.",
                            file=self.filepath,
                            line=lineno,
                            snippet=snippet,
                        )
                    )
            elif func_name in ("yaml.load", "ruamel.yaml.load"):
                # Check for Loader=SafeLoader
                has_safe_loader = any(
                    (
                        kw.arg == "Loader"
                        and isinstance(kw.value, ast.Attribute)
                        and "SafeLoader" in kw.value.attr
                    )
                    or (
                        kw.arg == "Loader"
                        and isinstance(kw.value, ast.Name)
                        and "SafeLoader" in kw.value.id
                    )
                    for kw in node.keywords
                )
                if not has_safe_loader:
                    self.findings.append(
                        SecurityFinding(
                            severity="HIGH",
                            category="Insecure Deserialization",
                            message=f"{func_name}() called without SafeLoader. Use yaml.safe_load() to prevent code execution.",
                            file=self.filepath,
                            line=lineno,
                            snippet=snippet,
                        )
                    )
            elif func_name.endswith(".connect") and "socket" in func_name:
                self.findings.append(
                    SecurityFinding(
                        severity="MEDIUM",
                        category="Outbound Network Socket",
                        message=f"Raw network socket connection '{func_name}()'. Verify outbound destination.",
                        file=self.filepath,
                        line=lineno,
                        snippet=snippet,
                    )
                )

        self.generic_visit(node)


class SecurityScanner:
    """
    Performs comprehensive static security checks on an app directory or archive
    before installation.
    """

    @staticmethod
    def scan_python_file(filepath: str, rel_path: str) -> List[SecurityFinding]:
        findings: List[SecurityFinding] = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            lines = content.splitlines()
            tree = ast.parse(content, filename=rel_path)
            visitor = CodeSecurityVisitor(filepath=rel_path, source_lines=lines)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        except SyntaxError as e:
            findings.append(
                SecurityFinding(
                    severity="HIGH",
                    category="Syntax Error",
                    message=f"Python syntax error: {str(e)}",
                    file=rel_path,
                    line=e.lineno or 1,
                )
            )
        except Exception as e:
            findings.append(
                SecurityFinding(
                    severity="MEDIUM",
                    category="File Parse Warning",
                    message=f"Could not parse file: {str(e)}",
                    file=rel_path,
                )
            )
        return findings

    @staticmethod
    def scan_requirements(req_path: str, rel_path: str) -> List[SecurityFinding]:
        findings: List[SecurityFinding] = []
        if not os.path.exists(req_path):
            return findings

        try:
            with open(req_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            for idx, line in enumerate(lines, start=1):
                clean = line.strip()
                if not clean or clean.startswith("#"):
                    continue

                for flag, severity, msg in SUSPICIOUS_REQUIREMENTS_FLAGS:
                    if clean.startswith(flag) or f" {flag}" in clean:
                        findings.append(
                            SecurityFinding(
                                severity=severity,
                                category="Dependency Injection Risk",
                                message=f"requirements.txt contains '{flag}': {msg}",
                                file=rel_path,
                                line=idx,
                                snippet=clean,
                            )
                        )

                # Check for direct git / http URLs in dependencies
                if re.match(r"^(git\+|http://|https://|ssh://)", clean):
                    findings.append(
                        SecurityFinding(
                            severity="MEDIUM",
                            category="Remote Dependency URL",
                            message=f"Package uses direct remote repository URL '{clean}'.",
                            file=rel_path,
                            line=idx,
                            snippet=clean,
                        )
                    )
        except Exception as e:
            findings.append(
                SecurityFinding(
                    severity="LOW",
                    category="Requirements File Parse Error",
                    message=f"Could not read requirements.txt: {e}",
                    file=rel_path,
                )
            )
        return findings

    @classmethod
    def scan_directory(cls, target_dir: str) -> SecurityScanReport:
        findings: List[SecurityFinding] = []
        files_scanned = 0
        py_files_scanned = 0

        abs_target = os.path.abspath(target_dir)

        # 1. Walk directory and scan files
        for root, dirs, files in os.walk(target_dir):
            # Skip virtual environments, git dirs, and OS metadata
            dirs[:] = [
                d
                for d in dirs
                if d
                not in (".git", "__pycache__", ".pytest_cache", "venv", "env", ".env", "__MACOSX")
            ]

            for filename in files:
                # Skip OS metadata and resource fork files
                if filename in (".DS_Store", "Thumbs.db") or filename.startswith("._"):
                    continue

                files_scanned += 1
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, abs_target)
                _, ext = os.path.splitext(filename.lower())

                # Check for forbidden binary and executable script formats
                if ext in FORBIDDEN_EXTENSIONS:
                    findings.append(
                        SecurityFinding(
                            severity="CRITICAL",
                            category="Forbidden Binary / Executable",
                            message=f"Forbidden file extension '{ext}'. Sub-app packages must not contain executable binaries or scripts.",
                            file=rel_path,
                        )
                    )
                    continue

                # Scan Python files
                if ext == ".py":
                    py_files_scanned += 1
                    file_findings = cls.scan_python_file(filepath, rel_path)
                    findings.extend(file_findings)

                # Scan requirements files
                elif filename in ("requirements.txt", "requirements-dev.txt"):
                    req_findings = cls.scan_requirements(filepath, rel_path)
                    findings.extend(req_findings)

        # 2. Check manifest.json or discovery
        from appmanager.admin.app_installer import parse_manifest

        manifest_data = parse_manifest(target_dir) or {}

        slug = manifest_data.get("slug", "")
        if slug and not re.match(r"^[a-z0-9\-_]+$", slug):
            findings.append(
                SecurityFinding(
                    severity="HIGH",
                    category="Invalid Slug Identifier",
                    message=f"Slug '{slug}' contains invalid characters. Slugs must only contain lowercase alphanumeric characters, dashes, and underscores.",
                    file="manifest.json",
                )
            )

        # Check entrypoint
        entry_point = manifest_data.get("entry_point", "")
        if entry_point:
            is_safe, ep_msg = validate_entrypoint_path(target_dir, entry_point)
            if not is_safe:
                findings.append(
                    SecurityFinding(
                        severity="CRITICAL",
                        category="Entrypoint Path Traversal",
                        message=ep_msg,
                        file="manifest.json",
                    )
                )

        # 3. Determine Overall Risk Level
        has_critical = any(f.severity == "CRITICAL" for f in findings)
        has_high = any(f.severity == "HIGH" for f in findings)
        has_medium = any(f.severity == "MEDIUM" for f in findings)
        has_low = any(f.severity == "LOW" for f in findings)

        if has_critical:
            risk_level = "CRITICAL"
            is_safe = False
        elif has_high:
            risk_level = "HIGH"
            is_safe = False
        elif has_medium:
            risk_level = "MEDIUM"
            is_safe = True  # Allowed with warning/confirmation
        elif has_low or findings:
            risk_level = "LOW"
            is_safe = True
        else:
            risk_level = "CLEAN"
            is_safe = True

        critical_cnt = sum(1 for f in findings if f.severity == "CRITICAL")
        high_cnt = sum(1 for f in findings if f.severity == "HIGH")
        med_cnt = sum(1 for f in findings if f.severity == "MEDIUM")

        if not findings:
            summary = f"Passed all checks cleanly. {files_scanned} files inspected ({py_files_scanned} Python modules)."
        else:
            summary = (
                f"Security scan found {len(findings)} potential issue(s): "
                f"{critical_cnt} critical, {high_cnt} high, {med_cnt} medium. "
                f"Total files scanned: {files_scanned}."
            )

        return SecurityScanReport(
            is_safe=is_safe,
            risk_level=risk_level,
            files_scanned=files_scanned,
            py_files_scanned=py_files_scanned,
            findings=findings,
            manifest_info=manifest_data,
            summary=summary,
        )


def run_security_scan(target_path: str) -> SecurityScanReport:
    """
    Main entry point for running the security scanner on a directory.
    """
    if not os.path.exists(target_path):
        return SecurityScanReport(
            is_safe=False,
            risk_level="CRITICAL",
            summary=f"Target path does not exist: {target_path}",
            findings=[
                SecurityFinding(
                    severity="CRITICAL",
                    category="Missing Files",
                    message=f"Target path '{target_path}' does not exist.",
                )
            ],
        )

    return SecurityScanner.scan_directory(target_path)
