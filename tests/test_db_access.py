import pytest

from appmanager import create_app
from appmanager.database import db
from appmanager.models import AppDbPermission, InstalledApp, User
from appmanager.db_access import (
    ensure_permission_rows,
    get_auth_context,
    get_db_engine,
    get_db_prefix,
    grant_permission,
    revoke_permission,
)


@pytest.fixture
def test_app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": "installed_apps",
            "SECRET_KEY": "test-secret-key-32-bytes-minimum-length",
            "JWT_SECRET": "test-jwt-secret-32-bytes-minimum-length",
        }
    )
    with app.app_context():
        db.create_all()
        user = User(email="admin@example.com", role="admin")
        app_rec = InstalledApp(
            name="DB App", slug="db-app", source_type="zip", is_active=True
        )
        db.session.add_all([user, app_rec])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def _app_id():
    return InstalledApp.query.filter_by(slug="db-app").first().id


def test_ensure_permission_rows_default_deny(test_app):
    with test_app.app_context():
        app_id = _app_id()
        manifest = {
            "requests_database": True,
            "database_access_level": "scoped",
            "requests_auth_readonly": True,
        }
        ensure_permission_rows(InstalledApp.query.get(app_id), manifest)

        db_perm = AppDbPermission.query.filter_by(app_id=app_id, permission_type="db").first()
        auth_perm = AppDbPermission.query.filter_by(
            app_id=app_id, permission_type="auth_readonly"
        ).first()

        assert db_perm is not None
        assert db_perm.granted is False
        assert db_perm.access_level == "scoped"
        assert db_perm.table_prefix == "app_db-app_"

        assert auth_perm is not None
        assert auth_perm.granted is False


def test_get_db_engine_none_when_denied(test_app):
    with test_app.app_context():
        app_id = _app_id()
        ensure_permission_rows(
            InstalledApp.query.get(app_id),
            {"requests_database": True, "database_access_level": "scoped"},
        )
        # Denied by default -> no engine.
        assert get_db_engine("db-app") is None
        assert get_db_prefix("db-app") is None


def test_grant_and_get_db_engine(test_app):
    with test_app.app_context():
        app_id = _app_id()
        ensure_permission_rows(
            InstalledApp.query.get(app_id),
            {"requests_database": True, "database_access_level": "scoped"},
        )
        grant_permission(app_id, "db", "scoped", admin_user_id=1)

        engine = get_db_engine("db-app")
        assert engine is not None
        assert get_db_prefix("db-app") == "app_db-app_"

        # Engine actually connects (SQLite in-memory).
        with engine.connect() as conn:
            conn.execute(db.text("SELECT 1"))


def test_revoke_permission(test_app):
    with test_app.app_context():
        app_id = _app_id()
        ensure_permission_rows(
            InstalledApp.query.get(app_id),
            {"requests_database": True, "database_access_level": "scoped"},
        )
        grant_permission(app_id, "db", "scoped", admin_user_id=1)
        assert get_db_engine("db-app") is not None

        revoke_permission(app_id, "db", admin_user_id=1)
        assert get_db_engine("db-app") is None


def test_auth_context_narrow_scope(test_app):
    with test_app.app_context():
        app_id = _app_id()
        ensure_permission_rows(
            InstalledApp.query.get(app_id), {"requests_auth_readonly": True}
        )
        # Denied by default -> None.
        assert get_auth_context("db-app", {}) is None

        grant_permission(app_id, "auth_readonly", "readonly", admin_user_id=1)

        headers = {
            "X-AppManager-User-Id": "5",
            "X-AppManager-User-Name": "Alice",
            "X-AppManager-User-Role": "admin",
        }
        ctx = get_auth_context("db-app", headers)
        assert ctx is not None
        assert ctx["authenticated"] is True
        assert ctx["display_name"] == "Alice"
        assert ctx["role"] == "admin"
        # Narrow scope: never exposes email or id.
        assert "email" not in ctx
        assert "id" not in ctx


def test_auth_context_requires_permission(test_app):
    with test_app.app_context():
        # No permission rows at all -> None.
        assert get_auth_context("db-app", {"X-AppManager-User-Id": "1"}) is None


def test_app_api_key_generate_and_validate(test_app):
    from appmanager.app_config import (
        generate_app_api_key,
        get_app_api_key,
        rotate_app_api_key,
        validate_app_api_key,
    )

    with test_app.app_context():
        app_id = _app_id()
        key = generate_app_api_key(app_id)
        assert key.startswith("amk_")
        assert get_app_api_key(app_id) == key
        assert validate_app_api_key(app_id, key) is True
        assert validate_app_api_key(app_id, "wrong-key") is False
        assert validate_app_api_key(app_id, "") is False

        # Rotation invalidates the old key.
        new_key = rotate_app_api_key(app_id)
        assert new_key != key
        assert validate_app_api_key(app_id, key) is False
        assert validate_app_api_key(app_id, new_key) is True


def test_app_api_key_installed_on_finalize(test_app):
    from appmanager.app_config import get_app_api_key

    with test_app.app_context():
        # The fixture creates an InstalledApp directly (not via installer), so
        # no key is generated. Verify the getter returns '' for a missing key.
        assert get_app_api_key(_app_id()) == ""


def test_audit_log_write_and_query(test_app):
    from appmanager.audit import log_action, query_audit

    with test_app.app_context():
        app_id = _app_id()
        log_action(
            "db_permission_grant",
            actor_type="admin",
            actor_id=1,
            app_id=app_id,
            details={"permission_type": "db", "access_level": "scoped"},
        )
        log_action("app_install", actor_type="admin", app_id=app_id, details={"slug": "db-app"})

        entries = query_audit(app_id=app_id)
        assert len(entries) == 2
        assert entries[0].action == "app_install"  # newest first

        filtered = query_audit(app_id=app_id, action="db_permission_grant")
        assert len(filtered) == 1
        assert filtered[0].details_json is not None


def test_grant_permission_writes_audit(test_app):
    from appmanager.audit import query_audit

    with test_app.app_context():
        app_id = _app_id()
        ensure_permission_rows(
            InstalledApp.query.get(app_id),
            {"requests_database": True, "database_access_level": "scoped"},
        )
        grant_permission(app_id, "db", "scoped", admin_user_id=1)

        entries = query_audit(app_id=app_id, action="db_permission_grant")
        assert len(entries) == 1
        assert entries[0].actor_id == 1


def test_rate_limiter_blocks_after_burst(test_app):
    from appmanager.ratelimit import allow, reset

    with test_app.app_context():
        reset()
        # Burst is 100 by default; hammer past it.
        allowed = sum(1 for _ in range(150) if allow("db-app", "report_event"))
        assert allowed == 100
        # A different action has its own bucket.
        assert allow("db-app", "other_action") is True
        # A different app is unaffected.
        assert allow("other-app", "report_event") is True
        reset()


def test_rate_limiter_can_be_disabled(test_app):
    from appmanager.ratelimit import allow, reset

    with test_app.app_context():
        reset()
        test_app.config["BRIDGE_RATE_LIMIT_ENABLED"] = False
        allowed = sum(1 for _ in range(150) if allow("db-app", "report_event"))
        assert allowed == 150
        test_app.config["BRIDGE_RATE_LIMIT_ENABLED"] = True
        reset()
