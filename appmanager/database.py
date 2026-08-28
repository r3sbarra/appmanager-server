from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        # Auto-migrate missing columns for SQLite
        try:
            from sqlalchemy import inspect, text

            engine = db.engine
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            with engine.connect() as conn:
                if "installed_apps" in tables:
                    columns = [c["name"] for c in inspector.get_columns("installed_apps")]
                    if "requires_auth" not in columns:
                        conn.execute(
                            text(
                                "ALTER TABLE installed_apps ADD COLUMN requires_auth BOOLEAN DEFAULT 1 NOT NULL"
                            )
                        )
                        conn.commit()
                    if "is_default" not in columns:
                        conn.execute(
                            text(
                                "ALTER TABLE installed_apps ADD COLUMN is_default BOOLEAN DEFAULT 0 NOT NULL"
                            )
                        )
                        conn.commit()
                    if "app_type" not in columns:
                        conn.execute(
                            text(
                                "ALTER TABLE installed_apps ADD COLUMN app_type VARCHAR(50) DEFAULT 'standalone' NOT NULL"
                            )
                        )
                        conn.commit()
                    if "target_app" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN target_app VARCHAR(100)")
                        )
                        conn.commit()
                    if "has_web_ui" not in columns:
                        conn.execute(
                            text(
                                "ALTER TABLE installed_apps ADD COLUMN has_web_ui BOOLEAN DEFAULT 1 NOT NULL"
                            )
                        )
                        conn.commit()
                    if "settings_json" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN settings_json TEXT")
                        )
                        conn.commit()
                    # SEO metadata columns (added 2026-08-28).
                    if "seo_title" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_title VARCHAR(255)")
                        )
                        conn.commit()
                    if "seo_description" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_description TEXT")
                        )
                        conn.commit()
                    if "seo_keywords" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_keywords TEXT")
                        )
                        conn.commit()
                    if "seo_canonical_url" not in columns:
                        conn.execute(
                            text(
                                "ALTER TABLE installed_apps ADD COLUMN seo_canonical_url VARCHAR(500)"
                            )
                        )
                        conn.commit()
                    if "seo_og_image" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_og_image VARCHAR(500)")
                        )
                        conn.commit()
                    if "seo_robots" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_robots VARCHAR(50)")
                        )
                        conn.commit()
                    if "seo_json_ld" not in columns:
                        conn.execute(
                            text("ALTER TABLE installed_apps ADD COLUMN seo_json_ld TEXT")
                        )
                        conn.commit()

                # Ensure the app_db_permissions table exists (created by db.create_all()
                # for fresh DBs; this is a safety net for existing DBs).
                if "app_db_permissions" not in tables:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS app_db_permissions (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                app_id INTEGER NOT NULL,
                                permission_type VARCHAR(32) NOT NULL DEFAULT 'db',
                                granted BOOLEAN NOT NULL DEFAULT 0,
                                access_level VARCHAR(16) NOT NULL DEFAULT 'scoped',
                                table_prefix VARCHAR(100),
                                granted_at DATETIME,
                                granted_by INTEGER,
                                revoked_at DATETIME,
                                revoked_by INTEGER,
                                CONSTRAINT uq_app_permission_type UNIQUE (app_id, permission_type)
                            )
                            """
                        )
                    )
                    conn.commit()

                # Ensure the audit_log table exists (safety net for existing DBs).
                if "audit_log" not in tables:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS audit_log (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                actor_type VARCHAR(16) NOT NULL DEFAULT 'admin',
                                actor_id INTEGER,
                                app_id INTEGER,
                                action VARCHAR(64) NOT NULL,
                                details_json TEXT
                            )
                            """
                        )
                    )
                    conn.commit()

                # Ensure the host_settings table exists (added 2026-08-28).
                if "host_settings" not in tables:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS host_settings (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                key VARCHAR(100) NOT NULL UNIQUE,
                                value TEXT,
                                updated_at DATETIME
                            )
                            """
                        )
                    )
                    conn.commit()

                if "users" in tables:
                    user_cols = [c["name"] for c in inspector.get_columns("users")]
                    if "last_login_at" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME"))
                        conn.commit()
                    if "last_active_at" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN last_active_at DATETIME"))
                        conn.commit()
                    if "login_count" not in user_cols:
                        conn.execute(
                            text(
                                "ALTER TABLE users ADD COLUMN login_count INTEGER DEFAULT 0 NOT NULL"
                            )
                        )
                        conn.commit()
                    if "last_ip" not in user_cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN last_ip VARCHAR(100)"))
                        conn.commit()
        except Exception as e:
            print(f"[DB MIGRATION WARNING] Failed auto-migration check: {e}")

        # Seed default system roles
        try:
            from appmanager.models import Role

            if Role.query.count() == 0:
                default_roles = [
                    Role(
                        name="Admin",
                        slug="admin",
                        description="System administrator with full permissions.",
                        is_system=True,
                    ),
                    Role(
                        name="User",
                        slug="user",
                        description="Standard member with assigned app permissions.",
                        is_system=True,
                    ),
                    Role(
                        name="Developer",
                        slug="developer",
                        description="Application developer with telemetry and API access.",
                        is_system=False,
                    ),
                    Role(
                        name="Manager",
                        slug="manager",
                        description="Team manager with supervisory access.",
                        is_system=False,
                    ),
                    Role(
                        name="Viewer",
                        slug="viewer",
                        description="Read-only access to authorized tools.",
                        is_system=False,
                    ),
                ]
                db.session.add_all(default_roles)
                db.session.commit()
        except Exception as e:
            print(f"[DB INIT WARNING] Role seeding check: {e}")
