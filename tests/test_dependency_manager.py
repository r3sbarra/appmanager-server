import platform

from appmanager.dependency_manager import (
    analyze_dependencies,
    analyze_uninstall_dependencies,
    check_python_version_compatibility,
    install_app_dependencies,
    parse_requirements_txt,
)


def test_python_version_compatibility():
    curr_major = platform.python_version_tuple()[0]
    curr_minor = int(platform.python_version_tuple()[1])

    # 1. Compatible specifiers
    is_ok, msg = check_python_version_compatibility(f">={curr_major}.{curr_minor}")
    assert is_ok is True
    assert "Current" in msg

    is_ok, msg = check_python_version_compatibility(">=3.8")
    assert is_ok is True

    is_ok, msg = check_python_version_compatibility(None)
    assert is_ok is True

    # 2. Incompatible specifiers
    is_ok, msg = check_python_version_compatibility(">=4.0")
    assert is_ok is False
    assert "mismatch" in msg.lower()

    is_ok, msg = check_python_version_compatibility("<3.6")
    assert is_ok is False


def test_parse_requirements_txt():
    sample_content = """
    # Comment line
    Flask>=2.0.0,<3.5
    requests==2.31.0 # inline comment
    sqlalchemy>=2.0
    --extra-index-url https://custom.pypi.org/simple
    -r other-requirements.txt
    pytest
    """
    reqs = parse_requirements_txt(sample_content)
    names = [r[0].lower() for r in reqs]

    assert "flask" in names
    assert "requests" in names
    assert "sqlalchemy" in names
    assert "pytest" in names
    assert "--extra-index-url" not in names


def test_analyze_dependencies_clean(tmp_path):
    app_dir = tmp_path / "sample_app"
    app_dir.mkdir()

    # App with compatible packages and python req
    (app_dir / "requirements.txt").write_text("pytest>=7.0.0\nrequests\n")
    manifest = {
        "name": "Sample Clean App",
        "slug": "sample-clean",
        "requires_python": ">=3.9",
    }

    report = analyze_dependencies(str(app_dir), manifest=manifest, venv_mode="singular")
    assert report.is_safe is True
    assert report.python_version_ok is True
    assert len(report.items) == 2
    assert len(report.conflicts) == 0


def test_analyze_dependencies_conflict_detection(tmp_path):
    app_dir = tmp_path / "conflict_app"
    app_dir.mkdir()

    # Flask is installed in environment as 3.x; app requests legacy 1.x
    (app_dir / "requirements.txt").write_text("Flask<2.0.0\n")
    manifest = {
        "name": "Legacy Conflict App",
        "slug": "legacy-conflict",
    }

    report = analyze_dependencies(str(app_dir), manifest=manifest, venv_mode="singular")
    assert report.is_safe is False  # Core package conflict blocks install in singular mode
    assert len(report.conflicts) > 0
    assert any("Flask" in c for c in report.conflicts)


def test_analyze_uninstall_dependencies_preservation(tmp_path):
    installed_dir = tmp_path / "installed_apps"
    installed_dir.mkdir()

    # App 1 has shared requests and unique foo
    app1 = installed_dir / "app1"
    app1.mkdir()
    (app1 / "requirements.txt").write_text("requests>=2.0\nfoo-package>=1.0\n")

    # App 2 also has requests
    app2 = installed_dir / "app2"
    app2.mkdir()
    (app2 / "requirements.txt").write_text("requests>=2.0\nbar-package>=1.0\n")

    summary = analyze_uninstall_dependencies("app1", str(installed_dir))
    assert summary["is_safe_to_uninstall"] is True
    assert "requests" in summary["shared_packages_preserved"]
    assert "foo-package" in summary["orphaned_packages"]
    assert "preserved" in summary["message"]


def test_install_app_dependencies_no_reqs(tmp_path):
    empty_dir = tmp_path / "empty_app"
    empty_dir.mkdir()
    ok, msg = install_app_dependencies(str(empty_dir), venv_mode="singular")
    assert ok is True
    assert "nothing to install" in msg
