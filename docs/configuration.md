# Configuration Reference

AppManager is configured through environment variables or a `.env` file located in the root directory.

---

## Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SECRET_KEY` | string | `appmanager-super-secret-...` | Flask session secret key. **Must be changed in production** (min 32 chars). |
| `JWT_SECRET` | string | `jwt-secret-key-...` | Secret key used for signing JWT tokens. |
| `JWT_ACCESS_TOKEN_EXPIRES_DAYS` | int | `7` | Number of days before JWT tokens expire. |
| `MAGIC_LINK_EXPIRES_MINUTES` | int | `15` | Expiration window for magic login link tokens. |
| `ALLOW_DEV_MAGIC_LOGIN` | bool | `false` | When `true`, displays one-click login button on page if SMTP is unconfigured. |
| `FIRST_USER_IS_ADMIN` | bool | `true` | When `true`, grants the admin role to the very first user who registers. |
| `ADMIN_EMAILS` | string | `""` | Comma-separated list of emails that automatically receive the admin role. |
| `DATABASE_URL` | string | `sqlite:///instance/appmanager.db` | Database connection string. |
| `APPMANAGER_BASE_DIR` | path | Current directory / repo root | Root directory used for relative storage paths. |
| `INSTALLED_APPS_DIR` | path | `${BASE_DIR}/installed_apps` | Directory where sub-application folders are located. |
| `TEMP_UPLOAD_DIR` | path | `${BASE_DIR}/instance/uploads` | Temporary upload staging folder for ZIP uploads. |
| `APP_BASE_URL` | url | `http://localhost:5000` | Publicly reachable base URL of the portal. |
| `PORT` | int | `5000` | Port for the local WSGI development server. |
| `HOST` | string | `0.0.0.0` | Bind host interface. |
| `TEMPLATES_AUTO_RELOAD` | bool | `true` | Enable Jinja2 template automatic reloading. |
| `SESSION_COOKIE_SAMESITE` | string | `Lax` | SameSite cookie policy (`Lax`, `Strict`, `None`). |
| `SESSION_COOKIE_SECURE` | bool | `false` | Set to `true` when running over HTTPS. |

---

## Database Configuration

AppManager uses **Flask-SQLAlchemy**. By default, it creates an SQLite database in the `instance/` folder.

### PostgreSQL

```env
DATABASE_URL=postgresql://user:password@localhost:5432/appmanager
```

### MySQL

```env
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/appmanager
```

---

## Authentication Configuration

### Google OAuth 2.0 (Optional)

To enable Google Single Sign-On:

1. Create OAuth credentials in the [Google Cloud Console](https://console.cloud.google.com/).
2. Add your authorized redirect URI: `<APP_BASE_URL>/auth/google/callback` (e.g. `http://localhost:5000/auth/google/callback`).
3. Set the following environment variables:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
```

### SMTP Email Settings for Magic Links

In production, AppManager sends one-time Magic Login Links to user inboxes via SMTP. If `SMTP_SERVER` is unconfigured and `ALLOW_DEV_MAGIC_LOGIN=false`, users will be prompted that email delivery is not configured.

For local development or staging testing without SMTP, you can set `ALLOW_DEV_MAGIC_LOGIN=true` to display a one-click login button directly in the browser and log the URL to stdout.

For production email delivery:

```env
SMTP_SERVER=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-smtp-password
MAIL_DEFAULT_SENDER=noreply@yourdomain.com
```

### Admin & Role Provisioning

- **Designated Admins**: Specify one or more admin email addresses using `ADMIN_EMAILS`:
  ```env
  ADMIN_EMAILS=admin@yourdomain.com,owner@yourdomain.com
  ```
- **First User Bootstrap**: By default, `FIRST_USER_IS_ADMIN=true` grants the `admin` role to the first account registered in a blank database. To disable this and enforce that all users start as standard `user` unless explicitly listed in `ADMIN_EMAILS` or elevated via CLI (`appmanager-server set-role user@example.com admin`):
  ```env
  FIRST_USER_IS_ADMIN=false
  ```
