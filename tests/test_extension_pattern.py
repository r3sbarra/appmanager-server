import pytest
from flask import Flask

from appmanager.database import db
from appmanager.extension import AppManager
from appmanager.models import InstalledApp


@pytest.fixture
def custom_app(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["BASE_DIR"] = str(tmp_path)
    app.config["INSTALLED_APPS_DIR"] = str(tmp_path / "installed_apps")
    app.config["TEMP_UPLOAD_DIR"] = str(tmp_path / "uploads")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'test.db'}"
    app.config["SECRET_KEY"] = "test-secret"
    app.config["JWT_SECRET"] = "test-jwt-secret"

    manager = AppManager()
    manager.init_app(app)

    with app.app_context():
        # Add sample app
        sample = InstalledApp(
            name="Extension Test App",
            slug="ext-test-app",
            description="Testing extension class",
            source_type="git",
            entry_point="app:app",
            is_active=True,
        )
        db.session.add(sample)
        db.session.commit()

    return app


def test_extension_init_and_storage(custom_app):
    assert "appmanager" in custom_app.extensions
    manager = custom_app.extensions["appmanager"]
    assert isinstance(manager, AppManager)


def test_extension_get_apps(custom_app):
    manager = custom_app.extensions["appmanager"]
    with custom_app.app_context():
        apps = manager.get_apps()
        assert len(apps) == 1
        assert apps[0].slug == "ext-test-app"

        single_app = manager.get_app("ext-test-app")
        assert single_app is not None
        assert single_app.name == "Extension Test App"


def test_extension_create_dispatcher(custom_app):
    manager = custom_app.extensions["appmanager"]
    dispatcher = manager.create_dispatcher(custom_app)
    assert dispatcher is not None
    assert hasattr(dispatcher, "clear_cache")

    # Test cache clearing
    manager.clear_cache(slug="ext-test-app")
