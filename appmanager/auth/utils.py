import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

import jwt
from flask import current_app, flash, g, redirect, request, url_for

from appmanager.database import db
from appmanager.models import MagicLinkToken, User

JWT_COOKIE_NAME = "appmanager_jwt"


def generate_jwt(user):
    payload = {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + current_app.config["JWT_ACCESS_TOKEN_EXPIRES"],
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET"], algorithm="HS256")
    return token


def decode_jwt(token):
    try:
        payload = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_current_user():
    if hasattr(g, "current_user") and g.current_user is not None:
        return g.current_user

    token = None
    # 1. Check Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]

    # 2. Check HTTP-only cookie
    if not token:
        token = request.cookies.get(JWT_COOKIE_NAME)

    if not token:
        g.current_user = None
        return None

    payload = decode_jwt(token)
    if not payload:
        g.current_user = None
        return None

    user = db.session.get(User, payload["user_id"])
    if user and user.is_active:
        # Update last_active_at timestamp (throttled to once per minute)
        now = datetime.now(timezone.utc)
        last_act = user.last_active_at
        if last_act and last_act.tzinfo is None:
            last_act = last_act.replace(tzinfo=timezone.utc)
        if not last_act or (now - last_act).total_seconds() > 60:
            user.last_active_at = now
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        g.current_user = user
        return user

    g.current_user = None
    return None


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.is_json or request.path.startswith("/api/"):
                return {"error": "Authentication required"}, 401
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.is_json or request.path.startswith("/api/"):
                return {"error": "Authentication required"}, 401
            return redirect(url_for("auth.login", next=request.url))
        if not user.is_admin():
            if request.is_json or request.path.startswith("/api/"):
                return {"error": "Admin privilege required"}, 403
            flash("Admin privilege required to access this area.", "danger")
            return redirect(url_for("auth.profile"))
        return f(*args, **kwargs)

    return decorated_function


def create_magic_link(email):
    token_str = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=current_app.config["MAGIC_LINK_EXPIRES_MINUTES"]
    )

    # Clean up old unused tokens for this email
    MagicLinkToken.query.filter_by(email=email, used=False).delete()

    magic_token = MagicLinkToken(email=email, token=token_str, expires_at=expires_at, used=False)
    db.session.add(magic_token)
    db.session.commit()

    if request:
        base_url = request.host_url.rstrip("/")
    else:
        base_url = current_app.config["APP_BASE_URL"].rstrip("/")

    verify_url = f"{base_url}/auth/verify-magic?token={token_str}"

    send_magic_link_email(email, verify_url)
    return verify_url


def send_magic_link_email(email, magic_url):
    smtp_server = current_app.config.get("SMTP_SERVER")
    if smtp_server:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Your Magic Login Link - AppManager"
            msg["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
            msg["To"] = email

            text = f"Click here to log into AppManager: {magic_url}\nThis link expires in {current_app.config['MAGIC_LINK_EXPIRES_MINUTES']} minutes."
            html = f"""
            <html>
              <body>
                <h2>AppManager Login</h2>
                <p>Click the button below to log in to your account. This link will expire in {current_app.config["MAGIC_LINK_EXPIRES_MINUTES"]} minutes.</p>
                <p><a href="{magic_url}" style="background-color: #4F46E5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Log In to AppManager</a></p>
                <p>Or copy this link into your browser: <br><code>{magic_url}</code></p>
              </body>
            </html>
            """
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(smtp_server, current_app.config["SMTP_PORT"]) as server:
                server.starttls()
                server.login(current_app.config["SMTP_USER"], current_app.config["SMTP_PASSWORD"])
                server.send_message(msg)
            print(f"[AUTH] Magic link email successfully sent to {email}")
        except Exception as e:
            print(
                f"[AUTH ERROR] Failed to send email via SMTP: {e}. Falling back to console output."
            )
            print(f"================ MAGIC LINK FOR {email} ================")
            print(f"URL: {magic_url}")
            print("=======================================================")
    else:
        print("\n================ MAGIC LINK (DEV CONSOLE LOG) ================")
        print(f"Target Email: {email}")
        print(f"Login URL:    {magic_url}")
        print("==============================================================\n")
