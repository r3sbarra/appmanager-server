"""
AppManager Developer SDK.

Re-exports the lightweight `appmanager_sdk` package components for sub-applications and extensions,
providing a single, unified developer API across the platform.
"""

from appmanager_sdk import (
    AdminSection,
    AppManager,
    AppManagerClient,
    AppManifest,
    ScheduledTask,
    Setting,
    client,
    get_current_user,
    require_auth,
)

__all__ = [
    "AppManifest",
    "Setting",
    "AdminSection",
    "ScheduledTask",
    "AppManagerClient",
    "AppManager",
    "client",
    "get_current_user",
    "require_auth",
]
