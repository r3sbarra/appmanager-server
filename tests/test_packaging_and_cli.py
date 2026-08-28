import pytest

from appmanager import (
    AppManager,
    DynamicAppDispatcherMiddleware,
    __version__,
    create_app,
    create_dispatchable_app,
    signals,
)
from appmanager.cli import main


def test_package_exports():
    assert __version__ == "0.2.0"
    assert callable(create_app)
    assert callable(create_dispatchable_app)
    assert DynamicAppDispatcherMiddleware is not None
    assert AppManager is not None
    assert signals is not None


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "0.2.0" in captured.out or "0.2.0" in captured.err


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "AppManager CLI" in captured.out
    assert "run" in captured.out
    assert "seed" in captured.out
    assert "new-subapp" in captured.out
    assert "validate-subapp" in captured.out


def test_cli_list_apps_and_seed(capsys, monkeypatch, tmp_path):
    test_db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db}")
    monkeypatch.setenv("APPMANAGER_BASE_DIR", str(tmp_path))

    # Run seed via CLI
    ret = main(["seed"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "[SEED]" in captured.out

    # Run list-apps via CLI
    ret = main(["list-apps"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Registered Applications" in captured.out
    assert "sample-counter" in captured.out
    assert "template-app" in captured.out


def test_cli_init(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ret = main(["init"])
    assert ret == 0
    assert (tmp_path / "installed_apps").is_dir()
    assert (tmp_path / ".env").is_file()
    captured = capsys.readouterr()
    assert "Initialization complete" in captured.out


def test_cli_no_args(capsys):
    ret = main([])
    assert ret == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
