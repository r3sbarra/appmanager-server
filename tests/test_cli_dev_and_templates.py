import os
import shutil
import tempfile

from appmanager.admin.app_installer import validate_subapp_package
from appmanager.cli import list_hooks_cli, main, new_subapp


def test_cli_new_subapp_templates():
    temp_dir = tempfile.mkdtemp(prefix="appmanager_templates_")
    try:
        # 1. API template
        api_dir = os.path.join(temp_dir, "my-api")
        new_subapp("My API Service", slug="my-api", output_dir=api_dir, template="api")
        is_valid, errors, manifest = validate_subapp_package(api_dir)
        assert is_valid is True
        assert manifest["name"] == "My API Service"
        assert os.path.exists(os.path.join(api_dir, "app.py"))

        # 2. Extension template
        ext_dir = os.path.join(temp_dir, "my-extension")
        new_subapp(
            "My Extension Plugin", slug="my-extension", output_dir=ext_dir, template="extension"
        )
        is_valid_ext, errors_ext, manifest_ext = validate_subapp_package(ext_dir)
        assert is_valid_ext is True
        assert manifest_ext["app_type"] == "extension"
        assert "settings" in manifest_ext
        assert "banner_text" in manifest_ext["settings"]

        # 3. HTMX template
        htmx_dir = os.path.join(temp_dir, "my-htmx")
        new_subapp("My HTMX App", slug="my-htmx", output_dir=htmx_dir, template="htmx")
        is_valid_htmx, errors_htmx, manifest_htmx = validate_subapp_package(htmx_dir)
        assert is_valid_htmx is True
        assert os.path.exists(os.path.join(htmx_dir, "app.py"))

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_cli_hooks_command():
    ret = list_hooks_cli()
    assert ret == 0

    # Test invoking via main(['hooks'])
    exit_code = main(["hooks"])
    assert exit_code == 0
