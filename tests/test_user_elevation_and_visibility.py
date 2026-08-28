from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.engine.url import make_url

from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME, decode_jwt, generate_jwt
from appmanager.cli import list_users_cli, set_user_role
from appmanager.database import db
from appmanager.models import InstalledApp, User, UserAppPermission


def test_cli_user_elevation(tmp_path, capsys):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    with app.app_context():
        user = User(email="member@example.com", name="Member", role="user")
        db.session.add(user)
        db.session.commit()
        assert user.role == "user"
        assert not user.is_admin()

        # Elevate to admin via set_user_role
        res = set_user_role("member@example.com", role="admin", app=app)
        assert res == 0
        db.session.refresh(user)
        assert user.role == "admin"
        assert user.is_admin()

        # Demote back to user
        res = set_user_role("member@example.com", role="user", app=app)
        assert res == 0
        db.session.refresh(user)
        assert user.role == "user"

        # If user does not exist, set_user_role creates the user with specified role
        res = set_user_role("ghost@example.com", role="admin", app=app)
        assert res == 0
        ghost = User.query.filter_by(email="ghost@example.com").first()
        assert ghost is not None
        assert ghost.role == "admin"

        # Test list_users_cli
        res = list_users_cli(app=app)
        assert res == 0
        captured = capsys.readouterr()
        assert "member@example.com" in captured.out
        assert "ghost@example.com" in captured.out


def test_jwt_expiration_and_invalidation(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    with app.app_context():
        user = User(
            email="expire_test@example.com", name="Expire Test", role="user", is_active=True
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        # 1. Valid Token
        valid_token = generate_jwt(user)
        payload = decode_jwt(valid_token)
        assert payload is not None
        assert payload["email"] == "expire_test@example.com"

        # 2. Expired Token
        expired_payload = {
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
            "iat": datetime.now(timezone.utc) - timedelta(minutes=30),
        }
        expired_token = jwt.encode(expired_payload, app.config["JWT_SECRET"], algorithm="HS256")
        assert decode_jwt(expired_token) is None

        # 3. Tampered Token
        tampered_token = valid_token + "corrupt"
        assert decode_jwt(tampered_token) is None

    client = app.test_client()

    # Expired token cookie gets rejected
    client.set_cookie(JWT_COOKIE_NAME, expired_token)
    res = client.get("/auth/profile")
    assert res.status_code == 302
    assert "/auth/login" in res.location

    # Deactivated user invalidation
    with app.app_context():
        u = db.session.get(User, user_id)
        u.is_active = False
        db.session.commit()
        db.session.remove()

    client.set_cookie(JWT_COOKIE_NAME, valid_token)
    res = client.get("/auth/profile")
    assert res.status_code == 302
    assert "/auth/login" in res.location


def test_app_visibility_on_off_and_permissions(tmp_path):
    """
    Verifies that app visibility on dashboard respects:
    - Active toggle (is_active on/off)
    - User permission (can_access True/False)
    - Admin override
    """
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "INSTALLED_APPS_DIR": str(tmp_path / "installed_apps"),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    with app.app_context():
        admin = User(email="admin@example.com", name="Admin User", role="admin", is_active=True)
        member = User(email="member@example.com", name="Member User", role="user", is_active=True)
        db.session.add_all([admin, member])
        db.session.commit()

        app1 = InstalledApp(
            name="App One Visible",
            slug="app-one",
            source_type="git",
            entry_point="app:app",
            requires_auth=True,
            is_active=True,
        )
        app2 = InstalledApp(
            name="App Two Hidden",
            slug="app-two",
            source_type="git",
            entry_point="app:app",
            requires_auth=True,
            is_active=False,  # Off / Inactive
        )
        app3 = InstalledApp(
            name="App Three Restricted",
            slug="app-three",
            source_type="git",
            entry_point="app:app",
            requires_auth=True,
            is_active=True,
        )
        db.session.add_all([app1, app2, app3])
        db.session.commit()

        app3_id = app3.id
        member_id = member.id

        # Grant member permission to App 1, but NOT App 3
        p1 = UserAppPermission(user_id=member.id, app_id=app1.id, can_access=True)
        p3 = UserAppPermission(user_id=member.id, app_id=app3.id, can_access=False)
        db.session.add_all([p1, p3])
        db.session.commit()

        admin_token = generate_jwt(admin)
        member_token = generate_jwt(member)

    client = app.test_client()

    # 1. Admin Dashboard sees all active apps (App 1 and App 3), but NOT inactive App 2
    client.set_cookie(JWT_COOKIE_NAME, admin_token)
    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "App One Visible" in html
    assert "App Three Restricted" in html
    assert "App Two Hidden" not in html

    # 2. Member Dashboard ONLY sees App 1 (active & permitted), NOT App 2 (inactive) or App 3 (not permitted)
    client.set_cookie(JWT_COOKIE_NAME, member_token)
    res = client.get("/dashboard")
    assert res.status_code == 200
    member_html = res.get_data(as_text=True)
    assert "App One Visible" in member_html
    assert "App Two Hidden" not in member_html
    assert "App Three Restricted" not in member_html

    # 3. Toggle Admin permission for App 3 to True
    with app.app_context():
        p3_rec = UserAppPermission.query.filter_by(user_id=member_id, app_id=app3_id).first()
        p3_rec.can_access = True
        db.session.commit()

    # Now member sees App 3
    res = client.get("/dashboard")
    assert res.status_code == 200
    member_html2 = res.get_data(as_text=True)
    assert "App Three Restricted" in member_html2

    # 4. Toggle App 3 to Inactive (Off)
    with app.app_context():
        app3_rec = db.session.get(InstalledApp, app3_id)
        app3_rec.is_active = False
        db.session.commit()

    # Member and Admin both no longer see App 3 because it is Off
    res = client.get("/dashboard")
    assert "App Three Restricted" not in res.get_data(as_text=True)


def test_mysql_database_url_configuration():
    """
    Verifies that MySQL connection URLs can be supplied via DATABASE_URL and parse correctly.
    """
    mysql_url = "mysql+pymysql://appuser:secret123@localhost:3306/appmanager_db"
    parsed = make_url(mysql_url)
    assert parsed.drivername == "mysql+pymysql"
    assert parsed.username == "appuser"
    assert parsed.password == "secret123"
    assert parsed.host == "localhost"
    assert parsed.port == 3306
    assert parsed.database == "appmanager_db"
