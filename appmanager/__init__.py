from typing import Optional

from flask import Flask

from appmanager import hooks, sdk, signals
from appmanager.config import Config
from appmanager.database import db
from appmanager.extension import AppManager
from appmanager.middleware import DynamicAppDispatcherMiddleware

__version__ = "0.3.1"

__all__ = [
    "AppManager",
    "create_app",
    "create_dispatchable_app",
    "signals",
    "hooks",
    "sdk",
    "db",
    "Config",
    "__version__",
    "DynamicAppDispatcherMiddleware",
]


def create_app(config_class=Config) -> Flask:
    """
    Application factory for AppManager host portal.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    if isinstance(config_class, dict):
        app.config.from_mapping(config_class)
    elif config_class and config_class != Config:
        app.config.from_object(config_class)

    # Initialize via AppManager extension
    AppManager(app)

    return app


def create_dispatchable_app(flask_app: Optional[Flask] = None) -> DynamicAppDispatcherMiddleware:
    """
    Creates and returns the dispatchable WSGI application wrapped in DynamicAppDispatcherMiddleware.
    """
    if flask_app is None:
        flask_app = create_app()
    if hasattr(flask_app, "extensions") and "appmanager" in flask_app.extensions:
        return flask_app.extensions["appmanager"].create_dispatcher(flask_app)
    return DynamicAppDispatcherMiddleware(flask_app)
