"""Login page and login/logout API endpoints."""
import os

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)

from player.api import AplusAPI

bp = Blueprint("auth", __name__)


def _saved_credentials():
    """Return optional credentials for a private, single-user deployment.

    Set APLUS_MOBILE and APLUS_PASSWORD in the environment (via .env or
    docker-compose) to skip the login form. Both must be present; otherwise the
    normal per-user login flow is used.
    """
    mobile = os.environ.get("APLUS_MOBILE", "").strip()
    password = os.environ.get("APLUS_PASSWORD", "")
    if mobile and password:
        return mobile, password
    return None


def _start_session(mobile, password):
    """Authenticate against Aplus and store the token in this browser session."""
    token = AplusAPI().login(mobile, password)
    session["token"] = token
    session["mobile"] = mobile
    session.pop("skip_autologin", None)


@bp.route("/login")
def login_page():
    """Auto-login from saved credentials, or render the login form.

    A manual logout is respected: the form is shown again instead of silently
    signing back in. Logging in manually re-enables auto-login next time.
    """
    creds = _saved_credentials()
    if creds and "token" not in session and not session.get("skip_autologin"):
        try:
            _start_session(*creds)
        except Exception as exc:  # noqa: BLE001 - show the reason on the form
            return render_template(
                "login.html",
                auto_login_error=f"Saved login failed: {exc}",
            )
        return redirect(url_for("courses.courses_page"))
    return render_template("login.html")


@bp.route("/api/login", methods=["POST"])
def api_login():
    """Authenticate against the Aplus API and start a Flask session."""
    data = request.json or {}
    mobile = data.get("mobile")
    password = data.get("password")

    if not mobile or not password:
        return jsonify({"error": "Mobile and password are required"}), 400

    try:
        _start_session(mobile, password)
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as-is
        return jsonify({"error": str(exc)}), 401

    return jsonify({"success": True, "token": session["token"]})


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    """Clear the Flask session (and don't immediately auto-login again)."""
    session.clear()
    if _saved_credentials():
        session["skip_autologin"] = True
    return jsonify({"success": True})


@bp.route("/api/user")
def api_user():
    """Return the current auth status.

    The frontend's checkAuth() polls this to decide whether to bounce the
    user to the login page: 401 when not logged in, 200 with the account
    details when they are.
    """
    if "token" not in session:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"authenticated": True, "mobile": session.get("mobile", "")})
