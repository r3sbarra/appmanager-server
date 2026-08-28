"""
In-process token-bucket rate limiting for sub-app bridge calls.

Prevents a single buggy or malicious sub-app from flooding the host with
telemetry / storage writes. Buckets are keyed per (app_slug, action) and
refill over a fixed window.

Defaults are configurable via the host config keys:
- ``BRIDGE_RATE_LIMIT_ENABLED`` (bool, default True)
- ``BRIDGE_RATE_LIMIT_RATE`` (tokens per second, default 100/60)
- ``BRIDGE_RATE_LIMIT_BURST`` (max burst, default 100)
"""

import threading
import time
from typing import Dict, Optional, Tuple

_lock = threading.Lock()
_buckets: Dict[str, Tuple[float, float]] = {}  # key -> (tokens, last_refill_ts)


def _config():
    from flask import current_app

    try:
        return {
            "enabled": bool(current_app.config.get("BRIDGE_RATE_LIMIT_ENABLED", True)),
            "rate": float(current_app.config.get("BRIDGE_RATE_LIMIT_RATE", 100.0 / 60.0)),
            "burst": int(current_app.config.get("BRIDGE_RATE_LIMIT_BURST", 100)),
        }
    except Exception:
        return {"enabled": True, "rate": 100.0 / 60.0, "burst": 100}


def allow(app_slug: str, action: str) -> bool:
    """
    Returns True if the (app, action) call is within its rate limit, False if it
    should be dropped. Thread-safe token bucket.
    """
    cfg = _config()
    if not cfg["enabled"]:
        return True

    rate = cfg["rate"]
    burst = cfg["burst"]
    key = f"{app_slug}:{action}"
    now = time.monotonic()

    with _lock:
        tokens, last = _buckets.get(key, (burst, now))
        # Refill based on elapsed time.
        elapsed = now - last
        tokens = min(burst, tokens + elapsed * rate)
        if tokens >= 1.0:
            _buckets[key] = (tokens - 1.0, now)
            return True
        _buckets[key] = (tokens, now)
        return False


def reset(app_slug: Optional[str] = None) -> None:
    """Clear rate-limit state (optionally for a single app). For tests / uninstall."""
    with _lock:
        if app_slug is None:
            _buckets.clear()
        else:
            for key in [k for k in _buckets if k.startswith(f"{app_slug}:")]:
                _buckets.pop(key, None)
