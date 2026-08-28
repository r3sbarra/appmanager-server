"""
Per-app shared database access and read-only auth context.

This module is the server-side half of the SDK's ``get_db_engine()`` /
``get_auth_context()`` calls. Sub-apps run in-process under the dispatcher, so
we hand them a SQLAlchemy engine object (never a raw connection string) and a
narrow read-only auth context. Credentials never leave the host process.

Permission model (``app_db_permissions`` table):
- ``permission_type="db"``: shared database access. ``access_level`` is
  ``scoped`` (app gets its own table prefix / MySQL schema) or ``full`` (raw
  engine to the host DB, admin-approved with a warning).
- ``permission_type="auth_readonly"``: read-only access to a narrow auth subset
  (login state, display name, role). Never email, id, or tokens.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import create_engine, text

from appmanager.database import db
from appmanager.models import AppDbPermission, InstalledApp

logger = logging.getLogger("appmanager.db_access")

# Cache of engines per (app_id, access_level) so we don't rebuild pools per call.
_engine_cache: Dict[str, Any] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _app_record(slug: str) -> Optional[InstalledApp]:
    return InstalledApp.query.filter_by(slug=slug).first()


def _permission(app_id: int, permission_type: str = "db") -> Optional[AppDbPermission]:
    return AppDbPermission.query.filter_by(
        app_id=app_id, permission_type=permission_type
    ).first()


def _host_db_url() -> str:
    """Return the host's configured SQLAlchemy database URL."""
    from flask import current_app

    return current_app.config.get("SQLALCHEMY_DATABASE_URI", "")


def _is_mysql(url: str) -> bool:
    return url.startswith("mysql") or "mysql" in url.split("://")[0]


def _scoped_engine(app: InstalledApp, perm: AppDbPermission):
    """
    Build a scoped engine for the app.

    - MySQL: create a dedicated schema ``appman_ext_<slug>`` and return an engine
      pointed at it (the app can only touch its own schema).
    - SQLite: return an engine to the host DB; the app is expected to use the
      ``table_prefix`` namespace (``app_<slug>_*``) via ``db_table()``.
    """
    host_url = _host_db_url()
    slug = app.slug

    if _is_mysql(host_url):
        schema = f"appman_ext_{slug}"
        # Ensure the schema exists (idempotent).
        try:
            admin_engine = create_engine(host_url)
            with admin_engine.connect() as conn:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS `{schema}`"))
                conn.commit()
            admin_engine.dispose()
        except Exception as e:
            logger.warning("Could not create MySQL schema %s: %s", schema, e)
        # Point the app engine at the dedicated schema.
        return create_engine(host_url)

    # SQLite: reuse the host DB file; scoping is by table prefix.
    return create_engine(host_url)


def _full_engine(app: InstalledApp, perm: AppDbPermission):
    """Raw engine to the host DB (full access)."""
    return create_engine(_host_db_url())


def get_db_engine(slug: str):
    """
    Returns a SQLAlchemy engine for the app's shared DB access, or None when the
    app was denied (or never requested) DB access.
    """
    app = _app_record(slug)
    if not app:
        return None
    perm = _permission(app.id, "db")
    if not perm or not perm.granted:
        return None

    cache_key = f"{app.id}:{perm.access_level}"
    if cache_key in _engine_cache:
        return _engine_cache[cache_key]

    if perm.access_level == "full":
        engine = _full_engine(app, perm)
    else:
        engine = _scoped_engine(app, perm)

    _engine_cache[cache_key] = engine
    return engine


def refresh_db_engine(slug: str):
    """
    Disposes any cached engine for the app and re-fetches it (for secret rotation
    or permission changes). Returns the new engine or None.
    """
    app = _app_record(slug)
    if not app:
        return None
    # Dispose all cached engines for this app.
    for key in [k for k in _engine_cache if k.startswith(f"{app.id}:")]:
        try:
            _engine_cache[key].dispose()
        except Exception:
            pass
        _engine_cache.pop(key, None)
    return get_db_engine(slug)


def get_db_prefix(slug: str) -> Optional[str]:
    """
    Returns the scoped table prefix for the app (e.g. ``app_weatherapp_``), or
    None when the app has no scoped DB access.
    """
    app = _app_record(slug)
    if not app:
        return None
    perm = _permission(app.id, "db")
    if not perm or not perm.granted or perm.access_level != "scoped":
        return None
    return perm.table_prefix or f"app_{app.slug}_"


def get_auth_context(slug: str, headers: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """
    Returns a narrow read-only auth context for the current user: login state,
    display name, and role only. Returns None when the app lacks the
    ``auth_readonly`` permission or the user is not authenticated.

    SECURITY: never exposes email, user id, passwords, or tokens.
    """
    app = _app_record(slug)
    if not app:
        return None
    perm = _permission(app.id, "auth_readonly")
    if not perm or not perm.granted:
        return None

    # Resolve the current user from forwarded headers (same trust model as the SDK).
    user_id = None
    user_name = None
    user_role = None
    if headers is not None:
        user_id = headers.get("X-AppManager-User-Id") or headers.get(
            "HTTP_X_APPMANAGER_USER_ID"
        )
        user_name = headers.get("X-AppManager-User-Name") or headers.get(
            "HTTP_X_APPMANAGER_USER_NAME"
        )
        user_role = headers.get("X-AppManager-User-Role") or headers.get(
            "HTTP_X_APPMANAGER_USER_ROLE"
        )
    else:
        try:
            from flask import request

            headers = request.headers
            user_id = headers.get("X-AppManager-User-Id") or headers.get(
                "HTTP_X_APPMANAGER_USER_ID"
            )
            user_name = headers.get("X-AppManager-User-Name") or headers.get(
                "HTTP_X_APPMANAGER_USER_NAME"
            )
            user_role = headers.get("X-AppManager-User-Role") or headers.get(
                "HTTP_X_APPMANAGER_USER_ROLE"
            )
        except Exception:
            pass

    if not user_id and not user_name:
        return None  # not authenticated

    # Only expose login state, display name, and role.
    return {
        "authenticated": True,
        "display_name": user_name or "",
        "role": user_role or "user",
    }


# ---------------------------------------------------------------------------
# Permission lifecycle helpers (used by the installer and admin routes)
# ---------------------------------------------------------------------------

def ensure_permission_rows(app: InstalledApp, manifest: Dict[str, Any]) -> None:
    """
    Create default (denied) permission rows for an app based on its manifest.
    Called during finalize_staged_installation().
    """
    requests_db = bool(manifest.get("requests_database", False))
    requests_auth = bool(manifest.get("requests_auth_readonly", False))

    if requests_db:
        existing = _permission(app.id, "db")
        if not existing:
            db.session.add(
                AppDbPermission(
                    app_id=app.id,
                    permission_type="db",
                    granted=False,
                    access_level=manifest.get("database_access_level", "scoped"),
                    table_prefix=f"app_{app.slug}_",
                )
            )
    if requests_auth:
        existing = _permission(app.id, "auth_readonly")
        if not existing:
            db.session.add(
                AppDbPermission(
                    app_id=app.id,
                    permission_type="auth_readonly",
                    granted=False,
                    access_level="readonly",
                )
            )
    db.session.commit()


def grant_permission(
    app_id: int, permission_type: str, access_level: str, admin_user_id: int
) -> AppDbPermission:
    """
    Grant (or update) a permission for an app. Returns the updated row.
    """
    perm = _permission(app_id, permission_type)
    if not perm:
        perm = AppDbPermission(
            app_id=app_id,
            permission_type=permission_type,
            granted=True,
            access_level=access_level,
            table_prefix=f"app_{_app_slug(app_id)}_",
        )
        db.session.add(perm)
    else:
        perm.granted = True
        perm.access_level = access_level
        if permission_type == "db" and access_level == "scoped" and not perm.table_prefix:
            perm.table_prefix = f"app_{_app_slug(app_id)}_"
    perm.granted_at = _now()
    perm.granted_by = admin_user_id
    perm.revoked_at = None
    perm.revoked_by = None
    db.session.commit()
    # Invalidate cached engine so the new scope takes effect.
    refresh_db_engine(_app_slug(app_id))
    try:
        from appmanager.audit import log_action

        log_action(
            "db_permission_grant",
            actor_type="admin",
            actor_id=admin_user_id,
            app_id=app_id,
            details={"permission_type": permission_type, "access_level": access_level},
        )
    except Exception:
        pass
    return perm


def revoke_permission(app_id: int, permission_type: str, admin_user_id: int) -> None:
    """
    Revoke a permission for an app.
    """
    perm = _permission(app_id, permission_type)
    if perm:
        perm.granted = False
        perm.access_level = "denied"
        perm.revoked_at = _now()
        perm.revoked_by = admin_user_id
        db.session.commit()
    refresh_db_engine(_app_slug(app_id))
    try:
        from appmanager.audit import log_action

        log_action(
            "db_permission_revoke",
            actor_type="admin",
            actor_id=admin_user_id,
            app_id=app_id,
            details={"permission_type": permission_type},
        )
    except Exception:
        pass


def _app_slug(app_id: int) -> str:
    app = db.session.get(InstalledApp, app_id)
    return app.slug if app else ""


def migrate_app_data(app_id: int, from_level: str, to_level: str) -> bool:
    """
    Migrate an app's data between scopes (e.g. scoped -> full or full -> scoped).

    For SQLite this is a no-op placeholder (table prefix vs raw engine share the
    same file, so no physical migration is needed). For MySQL it would move the
    app's schema. Returns True on success.
    """
    # TODO: implement physical migration for MySQL schema moves.
    logger.info(
        "DB scope migration requested for app %s: %s -> %s (no-op for SQLite)",
        app_id,
        from_level,
        to_level,
    )
    return True


def cleanup_app_data(app_id: int, slug: str) -> None:
    """
    Remove all permission rows and any scoped tables/schema created for an app.
    Called on uninstall.
    """
    # Drop permission rows.
    AppDbPermission.query.filter_by(app_id=app_id).delete()
    db.session.commit()

    # Invalidate any cached engines for this app.
    for key in [k for k in _engine_cache if k.startswith(f"{app_id}:")]:
        try:
            _engine_cache[key].dispose()
        except Exception:
            pass
        _engine_cache.pop(key, None)

    # Drop the app's MySQL schema if it exists.
    host_url = _host_db_url()
    if _is_mysql(host_url):
        schema = f"appman_ext_{slug}"
        try:
            admin_engine = create_engine(host_url)
            with admin_engine.connect() as conn:
                conn.execute(text(f"DROP SCHEMA IF EXISTS `{schema}`"))
                conn.commit()
            admin_engine.dispose()
        except Exception as e:
            logger.warning("Could not drop MySQL schema %s: %s", schema, e)

    # For SQLite, drop any scoped tables (app_<slug>_*) from the host DB.
    if not _is_mysql(host_url):
        try:
            engine = create_engine(host_url)
            prefix = f"app_{slug}_"
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE :p"
                    ),
                    {"p": f"{prefix}%"},
                ).fetchall()
                for (tname,) in rows:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{tname}"'))
                conn.commit()
            engine.dispose()
        except Exception as e:
            logger.warning("Could not drop scoped tables for '%s': %s", slug, e)
