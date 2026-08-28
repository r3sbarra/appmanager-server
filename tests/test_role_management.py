from appmanager import create_app
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.cli import create_role_cli, list_roles_cli, set_user_role
from appmanager.database import db
from appmanager.models import Role, User


def test_role_model_and_seeding(tmp_path):
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
        roles = Role.query.all()
        assert len(roles) >= 2
        role_slugs = [r.slug for r in roles]
        assert "admin" in role_slugs
        assert "user" in role_slugs


def test_admin_role_management_routes(tmp_path):
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
        admin = User(
            email="superadmin@example.com", name="Super Admin", role="admin", is_active=True
        )
        db.session.add(admin)
        db.session.commit()
        admin_token = generate_jwt(admin)

    client = app.test_client()
    client.set_cookie(JWT_COOKIE_NAME, admin_token)

    # 1. View roles page
    res = client.get("/admin/roles")
    assert res.status_code == 200
    assert "Role Management" in res.get_data(as_text=True)

    # 2. Create custom role
    res = client.post(
        "/admin/roles/create",
        data={
            "name": "Data Analyst",
            "slug": "analyst",
            "description": "Responsible for viewing metrics and reporting.",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "Data Analyst" in res.get_data(as_text=True)

    with app.app_context():
        analyst = Role.query.filter_by(slug="analyst").first()
        assert analyst is not None
        assert analyst.name == "Data Analyst"
        assert not analyst.is_system

        # Assign user to this role
        member = User(
            email="analyst_user@example.com", name="Analyst User", role="analyst", is_active=True
        )
        db.session.add(member)
        db.session.commit()
        analyst_id = analyst.id

    # 3. Edit custom role
    res = client.post(
        f"/admin/roles/{analyst_id}/edit",
        data={"name": "Senior Data Analyst", "description": "Senior analytics team member."},
        follow_redirects=True,
    )
    assert res.status_code == 200

    with app.app_context():
        updated_analyst = db.session.get(Role, analyst_id)
        assert updated_analyst.name == "Senior Data Analyst"

    # 4. Delete custom role (should reassign user to 'user')
    res = client.post(f"/admin/roles/{analyst_id}/delete", follow_redirects=True)
    assert res.status_code == 200
    assert "Deleted role" in res.get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Role, analyst_id) is None
        member_check = User.query.filter_by(email="analyst_user@example.com").first()
        assert member_check.role == "user"

    # 5. Attempt to delete system role (admin) should be prevented
    with app.app_context():
        admin_role = Role.query.filter_by(slug="admin").first()
        admin_role_id = admin_role.id

    res = client.post(f"/admin/roles/{admin_role_id}/delete", follow_redirects=True)
    assert res.status_code == 200
    assert "Cannot delete system role" in res.get_data(as_text=True)


def test_api_role_endpoints(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
            "APPMANAGER_API_KEY": "test-api-secret-key-123",
        }
    )

    client = app.test_client()
    headers = {"X-API-Key": "test-api-secret-key-123"}

    # 1. List roles
    res = client.get("/api/v1/roles", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] >= 2

    # 2. Create role via API
    res = client.post(
        "/api/v1/roles",
        headers=headers,
        json={
            "name": "API Auditor",
            "slug": "api-auditor",
            "description": "Security and compliance auditor.",
        },
    )
    assert res.status_code == 201
    create_data = res.get_json()
    assert create_data["success"] is True
    assert create_data["role"]["slug"] == "api-auditor"

    # Duplicate creation should return 409
    res = client.post(
        "/api/v1/roles", headers=headers, json={"name": "API Auditor", "slug": "api-auditor"}
    )
    assert res.status_code == 409

    # 3. Delete role via API
    res = client.delete("/api/v1/roles/api-auditor", headers=headers)
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_cli_role_management(tmp_path, capsys):
    app = create_app(
        {
            "TESTING": True,
            "BASE_DIR": str(tmp_path),
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SECRET_KEY": "test-secret-key-at-least-32-chars-long!",
            "JWT_SECRET": "test-jwt-secret-key-at-least-32-chars!",
        }
    )

    # 1. Create role via CLI
    res = create_role_cli(
        name="DevOps Engineer", slug="devops", description="Cloud and CI/CD manager", app=app
    )
    assert res == 0

    # 2. Assign user to new role
    res = set_user_role("devops@example.com", role="devops", app=app)
    assert res == 0

    with app.app_context():
        u = User.query.filter_by(email="devops@example.com").first()
        assert u is not None
        assert u.role == "devops"

    # 3. List roles via CLI
    res = list_roles_cli(app=app)
    assert res == 0
    captured = capsys.readouterr()
    assert "DevOps Engineer" in captured.out
    assert "devops" in captured.out
