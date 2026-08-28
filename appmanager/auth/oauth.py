import secrets
import urllib.parse

import requests
from flask import current_app, request, session, url_for


def get_google_auth_url():
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        return None, "Google Client ID is not configured."

    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state

    redirect_uri = url_for("auth.google_callback", _external=True)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return auth_url, None


def process_google_callback():
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        return None, "Google Client ID or Secret missing."

    state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)

    if not state or state != saved_state:
        return None, "Invalid OAuth state parameter."

    code = request.args.get("code")
    if not code:
        return None, "Authorization code not provided by Google."

    redirect_uri = url_for("auth.google_callback", _external=True)

    # Exchange code for token
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    token_res = requests.post(token_url, data=token_data, timeout=10)
    if token_res.status_code != 200:
        return None, f"Failed to exchange token with Google: {token_res.text}"

    tokens = token_res.json()
    access_token = tokens.get("access_token")

    # Get User Info
    userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
    userinfo_res = requests.get(
        userinfo_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
    )
    if userinfo_res.status_code != 200:
        return None, "Failed to retrieve user profile from Google."

    user_data = userinfo_res.json()
    return user_data, None
