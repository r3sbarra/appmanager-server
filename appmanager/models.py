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
    # Declarative SEO metadata (parsed from the manifest's ``seo`` block).
    seo_title = db.Column(db.String(255), nullable=True)
    seo_description = db.Column(db.Text, nullable=True)
    seo_keywords = db.Column(db.Text, nullable=True)  # comma-separated
    seo_canonical_url = db.Column(db.String(500), nullable=True)
    seo_og_image = db.Column(db.String(500), nullable=True)
    seo_robots = db.Column(db.String(50), nullable=True)
    seo_json_ld = db.Column(db.Text, nullable=True)  # raw JSON-LD

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

    def get_seo(self):
        """
        Return the app's SEO metadata as a dict (or None if none declared).

        Mirrors the manifest ``seo`` block shape so the host can render it
        directly. ``seo_keywords`` is split back into a list.
        """
        if not any(
            [
                self.seo_title,
                self.seo_description,
                self.seo_keywords,
                self.seo_canonical_url,
                self.seo_og_image,
                self.seo_robots,
                self.seo_json_ld,
            ]
        ):
            return None
        keywords = None
        if self.seo_keywords:
            keywords = [k.strip() for k in self.seo_keywords.split(",") if k.strip()]
        return {
            "title": self.seo_title,
            "description": self.seo_description,
            "keywords": keywords,
            "canonical_url": self.seo_canonical_url,
            "og_image": self.seo_og_image,
            "robots": self.seo_robots,
            "json_ld": self.seo_json_ld,
        }

    def set_seo(self, seo_dict):
        """
        Persist a manifest ``seo`` block (dict) onto the SEO columns.

        Accepts None to clear all SEO fields. ``keywords`` (list or
        comma-separated string) is flattened to a comma-separated string.
        """
        if not seo_dict:
            self.seo_title = None
            self.seo_description = None
            self.seo_keywords = None
            self.seo_canonical_url = None
            self.seo_og_image = None
            self.seo_robots = None
            self.seo_json_ld = None
            return
        self.seo_title = seo_dict.get("title")
        self.seo_description = seo_dict.get("description")
        keywords = seo_dict.get("keywords")
        if isinstance(keywords, list):
            self.seo_keywords = ", ".join(keywords)
        elif isinstance(keywords, str):
            self.seo_keywords = keywords
        else:
            self.seo_keywords = None
        self.seo_canonical_url = seo_dict.get("canonical_url")
        self.seo_og_image = seo_dict.get("og_image")
        self.seo_robots = seo_dict.get("robots")
        json_ld = seo_dict.get("json_ld")
        if isinstance(json_ld, (dict, list)):
            import json

            self.seo_json_ld = json.dumps(json_ld)
        elif isinstance(json_ld, str):
            self.seo_json_ld = json_ld
        else:
            self.seo_json_ld = None

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
            "seo": self.get_seo(),
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


class AppDbPermission(db.Model):
    """
    Per-app permission to access the host's shared database and/or read-only
    auth context. Created when an app's manifest requests access; the admin
    approves/denies at install time and can adjust it later.

    ``permission_type`` is ``"db"`` (shared database access) or
    ``"auth_readonly"`` (read-only login state / display name / role).
    """

    __tablename__ = "app_db_permissions"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=False, index=True)
    permission_type = db.Column(db.String(32), nullable=False, default="db")  # db | auth_readonly
    granted = db.Column(db.Boolean, default=False, nullable=False)
    access_level = db.Column(
        db.String(16), default="scoped", nullable=False
    )  # scoped | full | denied
    table_prefix = db.Column(db.String(100), nullable=True)  # auto-assigned "app_<slug>_"
    granted_at = db.Column(db.DateTime, nullable=True)
    granted_by = db.Column(db.Integer, nullable=True)  # admin user id
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("app_id", "permission_type", name="uq_app_permission_type"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "app_id": self.app_id,
            "permission_type": self.permission_type,
            "granted": self.granted,
            "access_level": self.access_level,
            "table_prefix": self.table_prefix,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "granted_by": self.granted_by,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revoked_by": self.revoked_by,
        }


class AuditLog(db.Model):
    """
    Append-only audit trail of security-relevant actions: app install/uninstall,
    DB permission grants/revokes, config changes, API key rotation, etc.
    """

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.now(), nullable=False, index=True)
    actor_type = db.Column(db.String(16), nullable=False, default="admin")  # admin | system | app
    actor_id = db.Column(db.Integer, nullable=True)  # admin user id or app id
    app_id = db.Column(db.Integer, db.ForeignKey("installed_apps.id"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    details_json = db.Column(db.Text, nullable=True)

    def to_dict(self):
        import json

        details = None
        if self.details_json:
            try:
                details = json.loads(self.details_json)
            except Exception:
                details = self.details_json
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "app_id": self.app_id,
            "action": self.action,
            "details": details,
        }


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


class HostSetting(db.Model):
    """
    Typed host-level configuration store (distinct from per-app ``AppConfig``).

    Holds AppManager-wide settings such as SEO defaults, dashboard/login
    behavior, and app visibility. Each setting is a row keyed by name, so it is
    queryable and safe for concurrent updates. Values are stored as JSON and
    decoded on read.
    """

    __tablename__ = "host_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)  # JSON-encoded value
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def get_value(self):
        """Decode the stored JSON value into a Python object."""
        import json

        if self.value is None:
            return None
        try:
            return json.loads(self.value)
        except Exception:
            return self.value

    def set_value(self, val):
        """Encode a Python value as JSON for storage."""
        import json

        self.value = json.dumps(val) if val is not None else None

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.get_value(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
