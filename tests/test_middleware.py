import io
import os
import shutil
import zipfile

import pytest

from appmanager import create_app, create_dispatchable_app
from appmanager.admin.app_installer import install_from_zip
from appmanager.auth.utils import JWT_COOKIE_NAME, generate_jwt
from appmanager.database import db
from appmanager.models import User, UserAppPermission


@pytest.fixture
def app_setup():
    app_dir = "/tmp/appmanager_test_middleware_apps"
    upload_dir = "/tmp/appmanager_test_middleware_uploads"

    if os.path.exists(app_dir):
        shutil.rmtree(app_dir, ignore_errors=True)
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)

    os.makedirs(app_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)

    test_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INSTALLED_APPS_DIR": app_dir,
            "TEMP_UPLOAD_DIR": upload_dir,
            "SECRET_KEY": "test-key-32-bytes-long-secret-key-for-testing!",
            "JWT_SECRET": "test-jwt-secret-32-bytes-long-key-for-testing!",
        }
    )

    with test_app.app_context():
        db.create_all()

        # Install a sample Flask app
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            app_py = """from flask import Flask
app = Flask(__name__)
@app.route('/')
def home():
    return "Hello from Sub App Endpoint!"
"""
            zf.writestr("app.py", app_py)
        zip_buffer.seek(0)

        class DummyFileStorage:
            def __init__(self, buf, filename):
                self.buf = buf
                self.filename = filename

            def save(self, dst):
                with open(dst, "wb") as f:
                    f.write(self.buf.getvalue())

        installed_app = install_from_zip(
            DummyFileStorage(zip_buffer, "test.zip"), name="Test SubApp", slug="test-subapp"
        )

        # Create Admin user
        admin_user = User(email="admin@example.com", role="admin")
        # Create Normal User
        normal_user = User(email="user@example.com", role="user")

        db.session.add(admin_user)
        db.session.add(normal_user)
        db.session.commit()

        admin_id = admin_user.id
        user_id = normal_user.id
        app_id = installed_app.id

    wsgi_dispatchable = create_dispatchable_app(test_app)
    # Wrap test_app wsgi_app so Flask test client goes through the middleware
    test_app.wsgi_app = wsgi_dispatchable

    yield test_app, test_app.test_client(), admin_id, user_id, app_id

    if os.path.exists(app_dir):
        shutil.rmtree(app_dir, ignore_errors=True)
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)


def test_subapp_unauthenticated(app_setup):
    _, client, _, _, _ = app_setup
    res = client.get("/apps/test-subapp/")
    assert res.status_code == 401
    assert b"Authentication Required" in res.data


def test_subapp_admin_access(app_setup):
    app, client, admin_id, _, _ = app_setup
    with app.app_context():
        admin_user = db.session.get(User, admin_id)
        jwt_token = generate_jwt(admin_user)

    client.set_cookie(JWT_COOKIE_NAME, jwt_token)
    res = client.get("/apps/test-subapp/")
    assert res.status_code == 200
    assert b"Hello from Sub App Endpoint!" in res.data


def test_subapp_permission_denied(app_setup):
    app, client, _, user_id, app_id = app_setup
    with app.app_context():
        perm = UserAppPermission.query.filter_by(user_id=user_id, app_id=app_id).first()
        if not perm:
            perm = UserAppPermission(user_id=user_id, app_id=app_id, can_access=False)
            db.session.add(perm)
        else:
            perm.can_access = False
        db.session.commit()

        normal_user = db.session.get(User, user_id)
        jwt_token = generate_jwt(normal_user)

    client.set_cookie(JWT_COOKIE_NAME, jwt_token)
    res = client.get("/apps/test-subapp/")
    assert res.status_code == 403
    assert b"Access Denied" in res.data
