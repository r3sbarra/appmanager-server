"""
Regression tests for the remaining audit items:

* #5  pip in ``singular`` mode cannot change host core packages (constraints).
* #13 one manifest reader: Python-defined manifests are visible everywhere, not
      only at install time.
* #14 settings resolution: typed ``app_configs`` win over a stale
      ``settings_json`` blob, and migration is key-level idempotent.
"""

import json
import os

from appmanager import create_app
from appmanager.app_config import get_config, migrate_from_settings_json, set_config
from appmanager.database import db
from appmanager.dependency_manager import _core_constraint_path
from appmanager.manifest import load_manifest, read_manifest_json
from appmanager.models import InstalledApp


def _make_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'host.db'}",
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "TEMP_UPLOAD_DIR": str(tmp_path / "uploads"),
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )


# ---------------------------------------------------------------------------
# #5 core-package constraints
# ---------------------------------------------------------------------------


def test_constraint_pins_installed_core_package(tmp_path):
    path = _core_constraint_path(str(tmp_path), "flask")
    assert os.path.exists(path)
    content = open(path).read().strip()
    # Flask is installed in the test env, so it must be pinned, not left open.
    assert content.startswith("flask==")
    version = content.split("==", 1)[1]
    assert version and version[0].isdigit()


def test_constraint_empty_for_absent_package(tmp_path):
    path = _core_constraint_path(str(tmp_path), "definitely-not-installed-pkg-xyz")
    assert os.path.exists(path)
    assert open(path).read().strip() == ""


def test_constraint_name_is_sanitized(tmp_path):
    """A hostile package name cannot escape the constraint directory."""
    path = _core_constraint_path(str(tmp_path), "../../etc/passwd")
    assert os.path.dirname(path) == os.path.join(str(tmp_path), ".appmanager")
    assert ".." not in os.path.basename(path)


# ---------------------------------------------------------------------------
# #13 single manifest reader
# ---------------------------------------------------------------------------


def test_read_manifest_json_returns_none_when_absent(tmp_path):
    assert read_manifest_json(str(tmp_path)) is None


def test_read_manifest_json_rejects_non_object(tmp_path):
    (tmp_path / "manifest.json").write_text('["not", "an", "object"]')
    assert read_manifest_json(str(tmp_path)) is None


def test_load_manifest_prefers_json(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"name": "Json App", "slug": "json-app"}))
    assert load_manifest(str(tmp_path)) == {"name": "Json App", "slug": "json-app"}


def test_load_manifest_discovers_python_manifest(tmp_path):
    """
    The critical drift case: an app whose manifest is defined in Python had it
    honoured at install time, then ignored when the admin registry rendered.
    """
    (tmp_path / "app.py").write_text(
        "from appmanager_sdk.schema import AppManifest\n"
        "manifest = AppManifest(name='Python App', slug='python-app')\n"
        "app = None\n"
    )
    assert read_manifest_json(str(tmp_path)) is None  # no JSON file

    loaded = load_manifest(str(tmp_path))
    assert loaded.get("slug") == "python-app"
    assert loaded.get("name") == "Python App"


def test_load_manifest_can_skip_python_execution(tmp_path):
    """Callers that must not execute app code get {} instead."""
    (tmp_path / "app.py").write_text(
        "from appmanager_sdk.schema import AppManifest\n"
        "manifest = AppManifest(name='Python App', slug='python-app')\n"
    )
    assert load_manifest(str(tmp_path), use_python_discovery=False) == {}


def test_registry_and_installer_agree(tmp_path):
    """Both former parsers must now return the same answer for the same app."""
    from appmanager.admin.app_installer import parse_manifest as installer_parse
    from appmanager.admin.registry import parse_manifest as registry_parse

    (tmp_path / "manifest.json").write_text(json.dumps({"name": "Same", "slug": "same"}))
    assert (
        installer_parse(str(tmp_path))
        == registry_parse(str(tmp_path))
        == {
            "name": "Same",
            "slug": "same",
        }
    )

    # And for an app that declares nothing, only the empty-vs-None shape differs.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert installer_parse(str(empty)) is None
    assert registry_parse(str(empty)) == {}


def test_missing_directory_returns_empty(tmp_path):
    assert load_manifest(str(tmp_path / "nope")) == {}


# ---------------------------------------------------------------------------
# #14 settings resolution order + migration
# ---------------------------------------------------------------------------


def test_typed_config_wins_over_stale_blob(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        rec = InstalledApp(
            name="S",
            slug="settings-app",
            source_type="zip",
            settings_json=json.dumps({"theme": "dark"}),
        )
        db.session.add(rec)
        db.session.commit()
        set_config(rec.id, "theme", "light", value_type="string")

        assert get_config(rec.id, "theme") == "light"


def test_migration_is_key_level_idempotent(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        rec = InstalledApp(
            name="S",
            slug="mig-app",
            source_type="zip",
            settings_json=json.dumps({"a": 1, "b": 2}),
        )
        db.session.add(rec)
        db.session.commit()

        # First pass migrates both keys.
        assert migrate_from_settings_json() == 1
        assert get_config(rec.id, "a") == 1
        assert get_config(rec.id, "b") == 2

        # Second pass is a no-op.
        assert migrate_from_settings_json() == 0

        # A key added to the blob later is still migrated (previously it was not,
        # because the app already had config rows).
        rec.settings_json = json.dumps({"a": 1, "b": 2, "c": 3})
        db.session.commit()
        assert migrate_from_settings_json() == 1
        assert get_config(rec.id, "c") == 3


def test_migration_does_not_overwrite_existing_value(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        rec = InstalledApp(
            name="S", slug="mig2", source_type="zip", settings_json=json.dumps({"x": "old"})
        )
        db.session.add(rec)
        db.session.commit()
        set_config(rec.id, "x", "operator-set", value_type="string")

        migrate_from_settings_json()
        assert get_config(rec.id, "x") == "operator-set"
