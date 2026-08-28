from datetime import datetime, timezone

from appmanager.database import db


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description or "",
            "is_system": self.is_system,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    role = db.Column(db.String(50), nullable=False, default="user")  # 'admin' or 'user'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_active_at = db.Column(db.DateTime, nullable=True)
    login_count = db.Column(db.Integer, default=0, nullable=False)
    last_ip = db.Column(db.String(100), nullable=True)

    permissions = db.relationship("UserAppPermission", backref="user", cascade="all, delete-orphan")

    def is_admin(self):
        return self.role == "admin"

    def is_online(self):
        if not self.last_active_at:
            return False
        dt = self.last_active_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        return diff < 300  # 5 minutes

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name or self.email,
            "role": self.role,
            "is_active": self.is_active,
            "is_online": self.is_online(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
            "login_count": self.login_count,
            "last_ip": self.last_ip,
        }


class InstalledApp(db.Model):
    __tablename__ = "installed_apps"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(
        db.String(100), unique=True, nullable=False, index=True
    )  # URL path under /apps/<slug>
    description = db.Column(db.Text, nullable=True)
    source_type = db.Column(db.String(50), nullable=False)  # 'git' or 'zip'
    source_url = db.Column(db.String(500), nullable=True)  # Git repo URL or original ZIP filename
    entry_point = db.Column(
        db.String(255), nullable=False, default="app:app"
    )  # e.g., app:app or main:create_app()
    installed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)
    requires_auth = db.Column(db.Boolean, default=True, nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    app_type = db.Column(
        db.String(50), default="standalone", nullable=False
    )  # 'standalone' or 'extension'
    target_app = db.Column(
        db.String(100), nullable=True
    )  # Target app slug if app_type is 'extension'
    has_web_ui = db.Column(db.Boolean, default=True, nullable=False)
    settings_json = db.Column(db.Text, nullable=True)

    permissions = db.relationship("UserAppPermission", backref="app", cascade="all, delete-orphan")

    def get_settings(self):
        import json

        if self.settings_json:
            try:
                return json.loads(self.settings_json)
            except Exception:
                return {}
        return {}

    def set_settings(self, settings_dict):
        import json

        self.settings_json = json.dumps(settings_dict) if settings_dict is not None else None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description or "",
            "source_type": self.source_type,
            "source_url": self.source_url,
            "entry_point": self.entry_point,
            "installed_at": self.installed_at.isoformat() if self.installed_at else None,
            "is_active": self.is_active,
            "requires_auth": self.requires_auth,
            "is_default": self.is_default,
            "app_type": self.app_type,
            "target_app": self.target_app,
            "has_web_ui": self.has_web_ui,
            "settings": self.get_settings(),
        }


class UserAppPermission(db.Model):
    __tablename__ = "user_app_permissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=False)
    can_access = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("user_id", "app_id", name="_user_app_uc"),)


class MagicLinkToken(db.Model):
    __tablename__ = "magic_link_tokens"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(255), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AppHealthLog(db.Model):
    __tablename__ = "app_health_logs"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=False)
    status = db.Column(
        db.String(50), nullable=False, default="healthy"
    )  # healthy, degraded, unhealthy
    response_time_ms = db.Column(db.Float, nullable=True)
    details = db.Column(db.Text, nullable=True)
    checked_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    app = db.relationship(
        "InstalledApp", backref=db.backref("health_logs", cascade="all, delete-orphan")
    )


class AppTelemetryLog(db.Model):
    __tablename__ = "app_telemetry_logs"

    id = db.Column(db.Integer, primary_key=True)
    app_slug = db.Column(db.String(100), nullable=False, index=True)
    event_type = db.Column(db.String(100), nullable=False)  # event, metric, error
    payload_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AppExtensionData(db.Model):
    __tablename__ = "app_extension_data"

    id = db.Column(db.Integer, primary_key=True)
    extension_slug = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)  # e.g., 'user'
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    data_json = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint("extension_slug", "entity_type", "entity_id", name="_ext_entity_uc"),
    )


class AppConfig(db.Model):
    """
    Typed per-app configuration store. Replaces the freeform `settings_json`
    blob as the primary settings interface. Each setting is a row — queryable,
    auditable, and safe for concurrent updates (no read-modify-write on a blob).

    Extensions declare their config schema on install; the framework seeds
    default rows and renders a generated form (or the extension provides a
    custom admin panel).
    """

    __tablename__ = "app_configs"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=False, index=True)
    key = db.Column(db.String(64), nullable=False)
    value = db.Column(db.Text, nullable=True)  # JSON string or plain text
    value_type = db.Column(
        db.String(16), default="json", nullable=False
    )  # json|string|boolean|integer|color
    is_secret = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (db.UniqueConstraint("app_id", "key", name="uq_app_config_key"),)

    def get_value(self):
        """Decode the stored value into a Python object based on value_type."""
        import json

        if self.value is None:
            return None
        if self.value_type == "json":
            try:
                return json.loads(self.value)
            except Exception:
                return self.value
        if self.value_type == "boolean":
            return self.value.lower() in ("1", "true", "yes", "on")
        if self.value_type == "integer":
            try:
                return int(self.value)
            except Exception:
                return None
        return self.value  # string, color

    def set_value(self, val):
        """Encode a Python value into the stored string based on value_type."""
        import json

        if val is None:
            self.value = None
        elif self.value_type == "json":
            self.value = json.dumps(val) if not isinstance(val, str) else val
        elif self.value_type == "boolean":
            self.value = "1" if val else "0"
        elif self.value_type == "integer":
            self.value = str(int(val))
        else:
            self.value = str(val)

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.get_value(),
            "value_type": self.value_type,
            "is_secret": self.is_secret,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AppAdminPanel(db.Model):
    """
    Declared admin panel for an installed app, parsed from its manifest on
    install/upgrade. The framework mounts these at `/admin/apps/<slug>/<panel>`
    with admin auth enforced at mount time.
    """

    __tablename__ = "app_admin_panels"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=False, index=True)
    panel_id = db.Column(db.String(64), nullable=False)  # e.g. 'flair-presets'
    label = db.Column(db.String(100), nullable=False)  # e.g. 'Flair presets'
    icon = db.Column(db.String(50), nullable=True)
    endpoint = db.Column(
        db.String(255), nullable=True
    )  # 'blueprint_name:endpoint' or None for generated form
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (db.UniqueConstraint("app_id", "panel_id", name="uq_app_panel"),)

    def to_dict(self):
        return {
            "panel_id": self.panel_id,
            "label": self.label,
            "icon": self.icon,
            "endpoint": self.endpoint,
            "sort_order": self.sort_order,
        }
