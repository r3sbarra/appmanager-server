import logging
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from appmanager.auth.oauth import get_google_auth_url, process_google_callback
from appmanager.auth.utils import (
    JWT_COOKIE_NAME,
    create_magic_link,
    generate_jwt,
    get_current_user,
    login_required,
)
from appmanager.database import db
from appmanager.models import InstalledApp, MagicLinkToken, User, UserAppPermission
from appmanager.security import check_rate_limit, is_safe_redirect_url

logger = logging.getLogger("appmanager.auth")
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET"])
def login():
    user = get_current_user()
    if user:
        return redirect(url_for("auth.profile"))

    next_url = request.args.get("next", "")
    if next_url and not is_safe_redirect_url(next_url):
        next_url = ""
    google_configured = bool(current_app.config.get("GOOGLE_CLIENT_ID"))
    return render_template("auth/login.html", next=next_url, google_configured=google_configured)


def resolve_initial_user_role(email: str) -> str:
    """
    Resolves the initial role ('admin' or 'user') for a newly created user:
    1. Checks if email is in ADMIN_EMAILS.
    2. If FIRST_USER_IS_ADMIN is True and no users exist in the database yet, returns 'admin'.
    3. Defaults to 'user'.
    """
    clean_email = email.strip().lower()
    admin_emails = current_app.config.get("ADMIN_EMAILS", [])
    if clean_email in [e.strip().lower() for e in admin_emails if e.strip()]:
        return "admin"

    if current_app.config.get("FIRST_USER_IS_ADMIN", True):
        if User.query.count() == 0:
            return "admin"

    return "user"


@auth_bp.route("/magic-link", methods=["POST"])
def request_magic_link():
    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"magic_link_{client_ip}", limit=5, window_seconds=60):
        flash("Too many login requests. Please wait a minute and try again.", "danger")
        return redirect(url_for("auth.login"))

    email = request.form.get("email", "").strip().lower()
    next_url = request.form.get("next", "")
    if next_url and not is_safe_redirect_url(next_url):
        next_url = ""

    if not email or "@" not in email:
        flash("Please enter a valid email address.", "warning")
        return redirect(url_for("auth.login", next=next_url))

    smtp_configured = bool(current_app.config.get("SMTP_SERVER"))
    allow_dev_magic = current_app.config.get("ALLOW_DEV_MAGIC_LOGIN", False)

    # If SMTP is not configured and developer preview is disabled, block and show error
    if not smtp_configured and not allow_dev_magic:
        flash(
            "Email delivery (SMTP) is not configured on this server. "
            "Please configure SMTP settings in .env (or enable ALLOW_DEV_MAGIC_LOGIN=true for local testing).",
            "danger",
        )
        return redirect(url_for("auth.login", next=next_url))

    magic_url = create_magic_link(email)

    # In dev mode with ALLOW_DEV_MAGIC_LOGIN=True, pass the magic link URL to the template for quick login
    dev_magic_url = None
    if not smtp_configured and allow_dev_magic:
        dev_magic_url = magic_url

    return render_template(
        "auth/login.html",
        magic_sent=True,
        email=email,
        dev_magic_url=dev_magic_url,
        google_configured=bool(current_app.config.get("GOOGLE_CLIENT_ID")),
    )


@auth_bp.route("/verify-magic", methods=["GET"])
def verify_magic():
    token_str = request.args.get("token")
    if not token_str:
        flash("Invalid or missing magic link token.", "danger")
        return redirect(url_for("auth.login"))

    magic_token = MagicLinkToken.query.filter_by(token=token_str).first()
    if not magic_token or magic_token.used:
        flash("This magic link is invalid or has already been used.", "danger")
        return redirect(url_for("auth.login"))

    # Check expiration (naive/aware comparison handling)
    expires_at = magic_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        flash("This magic link has expired. Please request a new one.", "warning")
        return redirect(url_for("auth.login"))

    # Mark token as used
    magic_token.used = True
    db.session.commit()

    # Find or create user
    user = User.query.filter_by(email=magic_token.email).first()
    if not user:
        user_role = resolve_initial_user_role(magic_token.email)
        user = User(
            email=magic_token.email,
            name=magic_token.email.split("@")[0].capitalize(),
            role=user_role,
        )
        db.session.add(user)
        db.session.commit()

        # If non-admin user, assign default permissions for active apps
        if user_role != "admin":
            active_apps = InstalledApp.query.filter_by(is_active=True).all()
            for app in active_apps:
                perm = UserAppPermission(user_id=user.id, app_id=app.id, can_access=True)
                db.session.add(perm)
            db.session.commit()

    # Track login metrics
    user.last_login_at = datetime.now(timezone.utc)
    user.last_active_at = datetime.now(timezone.utc)
    user.login_count = (user.login_count or 0) + 1
    user.last_ip = request.remote_addr
    db.session.commit()

    jwt_token = generate_jwt(user)
    flash(f"Welcome back, {user.name}!", "success")

    response = make_response(redirect(url_for("auth.profile")))

    response.set_cookie(
        JWT_COOKIE_NAME,
        jwt_token,
        httponly=True,
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        max_age=7 * 24 * 3600,
    )
    return response


@auth_bp.route("/google")
def google_login():
    auth_url, error = get_google_auth_url()
    if error:
        flash(f"Google Login Error: {error}", "danger")
        return redirect(url_for("auth.login"))
    return redirect(auth_url)


@auth_bp.route("/google/callback")
def google_callback():
    user_data, error = process_google_callback()
    if error:
        flash(f"Google Login Failed: {error}", "danger")
        return redirect(url_for("auth.login"))

    email = user_data.get("email", "").lower()
    google_id = user_data.get("sub")
    name = user_data.get("name", email.split("@")[0])

    if not email:
        flash("Could not retrieve email from Google account.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter((User.google_id == google_id) | (User.email == email)).first()
    if not user:
        user_role = resolve_initial_user_role(email)
        user = User(email=email, name=name, google_id=google_id, role=user_role)
        db.session.add(user)
        db.session.commit()

        # If non-admin user, assign default permissions for active apps
        if user_role != "admin":
            active_apps = InstalledApp.query.filter_by(is_active=True).all()
            for app in active_apps:
                perm = UserAppPermission(user_id=user.id, app_id=app.id, can_access=True)
                db.session.add(perm)
            db.session.commit()
    else:
        if not user.google_id:
            user.google_id = google_id
            db.session.commit()

    jwt_token = generate_jwt(user)
    flash(f"Logged in with Google as {user.email}", "success")

    response = make_response(redirect(url_for("auth.profile")))
    response.set_cookie(
        JWT_COOKIE_NAME,
        jwt_token,
        httponly=True,
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        max_age=7 * 24 * 3600,
    )
    return response


@auth_bp.route("/logout")
def logout():
    flash("Logged out successfully.", "info")
    response = make_response(redirect(url_for("auth.login")))
    response.delete_cookie(JWT_COOKIE_NAME)
    return response


@auth_bp.route("/profile")
@login_required
def profile():
    user = get_current_user()
    if user.is_admin():
        accessible_apps = InstalledApp.query.filter_by(is_active=True).all()
    else:
        accessible_apps = (
            db.session.query(InstalledApp)
            .join(UserAppPermission, UserAppPermission.app_id == InstalledApp.id)
            .filter(
                UserAppPermission.user_id == user.id,
                UserAppPermission.can_access.is_(True),
                InstalledApp.is_active.is_(True),
            )
            .all()
        )

    return render_template("auth/profile.html", user=user, apps=accessible_apps)
