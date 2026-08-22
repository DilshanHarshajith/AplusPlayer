"""Auth guards shared by every blueprint.

Page routes redirect to the login page; API routes return 401 JSON.
Both were previously duplicated inline (`if 'token' not in session: ...`)
at the top of nearly every view function in app.py.
"""
from functools import wraps

from flask import jsonify, redirect, session, url_for


def login_required_page(view):
    """Redirect HTML page routes to the login page if not authenticated."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "token" not in session:
            return redirect(url_for("auth.login_page"))
        return view(*args, **kwargs)
    return wrapped


def login_required_api(view):
    """Return a 401 JSON error from API routes if not authenticated."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "token" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return view(*args, **kwargs)
    return wrapped
