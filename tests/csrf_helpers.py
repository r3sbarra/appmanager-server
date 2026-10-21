"""
CSRF helpers for the test suite.

The host enforces a CSRF token on every state-changing request (see
``AppManager._register_csrf_guard``). Tests that drive admin/auth routes as an
authenticated user must send a token, exactly like the browser forms do.

Token identity is resolved the same way the guard resolves it: the logged-in
user's id when the request carries a valid session cookie, otherwise the client
address. Because both depend on the request, the token is minted inside a throwaway
request context seeded with the client's *current* cookies and the same
``REMOTE_ADDR`` the real call will use. That keeps the helper honest when a test
logs a user in part-way through (e.g. magic-link flows).
"""

from appmanager.security import generate_csrf_token

DEFAULT_REMOTE_ADDR = "127.0.0.1"


def _client_cookies(client) -> dict:
    """Serialize the test client's current cookies into a Cookie header value."""
    parts = []
    try:
        for cookie in client._cookies.values():  # type: ignore[attr-defined]
            parts.append(f"{cookie.key}={cookie.value}")
    except Exception:
        return {}
    return {"HTTP_COOKIE": "; ".join(parts)} if parts else {}


def csrf_for(app, user=None, remote_addr=None, client=None) -> str:
    """
    Mint a CSRF token bound to the identity the guard will see for the request.

    Args:
        app: The Flask app (used to open a request context).
        user: Optional explicit user; its ``id`` becomes the token identity. Use
            this only when you know that user is what the request will resolve to.
        remote_addr: Client address. Defaults to the test client's ``127.0.0.1``.
        client: The Flask test client. Its current cookies are replayed into the
            minting context so a logged-in session binds the token to that user.

    Returns:
        A ``"<timestamp>:<signature>"`` token.
    """
    from flask import g

    addr = remote_addr or DEFAULT_REMOTE_ADDR
    environ = {"REMOTE_ADDR": addr}
    if client is not None:
        environ.update(_client_cookies(client))

    with app.test_request_context(environ_base=environ):
        if user is not None:
            g.current_user_id = user.id
        return generate_csrf_token()


def token_for_identity(app, identity: str) -> str:
    """Mint a token for an explicit identity string (used by focused tests)."""
    import hashlib
    import hmac
    import time

    secret = app.config["SECRET_KEY"]
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), f"{identity}:{timestamp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{timestamp}:{signature}"


def post_with_csrf(
    client,
    url,
    user=None,
    data=None,
    json=None,
    remote_addr=None,
    **kwargs,
):
    """
    POST to ``url`` with a CSRF token attached.

    Form data gets ``csrf_token``; JSON payloads get an ``X-CSRFToken`` header.
    ``user`` is optional — omit it for anonymous requests (e.g. ``/auth/magic-link``).
    """
    token = csrf_for(client.application, user=user, remote_addr=remote_addr, client=client)
    if json is not None:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-CSRFToken", token)
        if remote_addr:
            kwargs.setdefault("environ_base", {})["REMOTE_ADDR"] = remote_addr
        return client.post(url, json=json, headers=headers, **kwargs)
    form = dict(data or {})
    form.setdefault("csrf_token", token)
    if remote_addr:
        kwargs.setdefault("environ_base", {})["REMOTE_ADDR"] = remote_addr
    return client.post(url, data=form, **kwargs)


__all__ = ["csrf_for", "token_for_identity", "post_with_csrf", "generate_csrf_token"]
