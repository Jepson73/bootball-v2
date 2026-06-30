"""
v2/auth_v2.py — Auth decorator for web_ui_v2.

Replicates V1's mechanism (basic auth + 1h cookie) using the same env vars.
Does NOT import from scripts/web_ui.py.
"""
import os
import logging
from functools import wraps
from flask import request, make_response

logger = logging.getLogger(__name__)


def get_password() -> str | None:
    pw = os.environ.get("BOOTBALL_PASSWORD")
    if not pw:
        logger.critical("BOOTBALL_PASSWORD not set — all V2 auth will be denied")
    return pw


def require_auth(f):
    """Basic auth decorator — same mechanism as V1, independent implementation."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.cookies.get("authenticated_v2") == "true":
            return f(*args, **kwargs)
        auth = request.authorization
        pw = get_password()
        if not auth or auth.username != "bootball" or auth.password != pw:
            return make_response(
                "Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Bootball V2"'}
            )
        resp = make_response(f(*args, **kwargs))
        resp.set_cookie("authenticated_v2", "true", max_age=3600)
        return resp
    return decorated
