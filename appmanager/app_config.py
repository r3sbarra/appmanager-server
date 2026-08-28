"""
Typed per-app configuration helpers.

Provides CRUD over the `app_configs` table and a one-time migration from the
legacy `settings_json` blob. Extensions and the admin console both use these
helpers so settings stay consistent and queryable.
"""

from appmanager.database import db
from appmanager.models import AppConfig, InstalledApp


def get_config(app_id, key, default=None):
    """Fetch a single config value (decoded)."""
    row = AppConfig.query.filter_by(app_id=app_id, key=key).first()
    if row is None:
        return default
    return row.get_value()


def get_configs(app_id):
    """Fetch all configs for an app as {key: decoded_value}."""
    rows = AppConfig.query.filter_by(app_id=app_id).all()
    return {r.key: r.get_value() for r in rows}


def set_config(app_id, key, value, value_type="json", is_secret=False):
    """Create or update a single config row."""
    row = AppConfig.query.filter_by(app_id=app_id, key=key).first()
    if not row:
        row = AppConfig(app_id=app_id, key=key, value_type=value_type, is_secret=is_secret)
        db.session.add(row)
    row.set_value(value)
    db.session.commit()
    return row


def set_configs(app_id, config_dict, schema=None):
    """
    Bulk upsert configs from a dict. `schema` is an optional list of
    {key, type, default, is_secret} descriptors used to set value_type.
    """
    schema_map = {s.get("key"): s for s in (schema or [])}
    for key, value in config_dict.items():
        desc = schema_map.get(key, {})
        set_config(
            app_id,
            key,
            value,
            value_type=desc.get("type", "json"),
            is_secret=desc.get("is_secret", False),
        )


def seed_defaults(app_id, schema):
    """
    Seed default config rows for an app from a settings_schema manifest.
    Only creates rows that don't already exist (doesn't overwrite user edits).
    """
    for desc in schema or []:
        key = desc.get("key")
        if not key:
            continue
        existing = AppConfig.query.filter_by(app_id=app_id, key=key).first()
        if existing is None:
            row = AppConfig(
                app_id=app_id,
                key=key,
                value_type=desc.get("type", "json"),
                is_secret=desc.get("is_secret", False),
            )
            row.set_value(desc.get("default"))
            db.session.add(row)
    db.session.commit()


def migrate_from_settings_json(app_id=None):
    """
    One-time migration: parse each app's legacy `settings_json` blob and seed
    `app_configs` rows. Idempotent — skips apps that already have config rows.
    Returns number of apps migrated.
    """
    query = InstalledApp.query
    if app_id is not None:
        query = query.filter_by(id=app_id)
    apps = query.all()
    migrated = 0
    for a in apps:
        existing = AppConfig.query.filter_by(app_id=a.id).first()
        if existing is not None:
            continue
        settings = a.get_settings()
        if not settings:
            continue
        for key, value in settings.items():
            row = AppConfig(app_id=a.id, key=str(key), value_type="json")
            row.set_value(value)
            db.session.add(row)
        migrated += 1
    db.session.commit()
    return migrated
