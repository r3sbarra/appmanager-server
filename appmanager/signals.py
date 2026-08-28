"""
AppManager Signals
Provides blinker-based signal hooks for lifecycle and event monitoring.
"""

from blinker import Namespace

_signals = Namespace()

# Sub-App Lifecycle Signals
subapp_installed = _signals.signal("subapp-installed")
subapp_uninstalled = _signals.signal("subapp-uninstalled")
subapp_reloaded = _signals.signal("subapp-reloaded")

# Health Check Signals
health_check_completed = _signals.signal("health-check-completed")
health_check_failed = _signals.signal("health-check-failed")

# Telemetry & Auth Signals
telemetry_received = _signals.signal("telemetry-received")
user_logged_in = _signals.signal("user-logged-in")
