"""Login page and login/logout API endpoints."""
import os

from flask import Blueprint, jsonify, render_template, request, session

from player.api import AplusAPI

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    """Render the login page.

    Each user logs in with their own Aplus account, so the form is blank by
    default. If the operator wants the server's own credentials pre-filled
    (single-account convenience), they can set APLUS_PREFILL=1 — whatever
    the user actually submits always takes precedence.
    """
    prefill = os.environ.get("APLUS_PREFILL", "").lower() in ("1", "true", "yes")
    return render_template("login.html",
                           env_mobile=os.environ.get("APLUS_MOBILE", "") if prefill else "",
                           env_password=os.environ.get("APLUS_PASSWORD", "") if prefill else "")


@bp.route("/api/login", methods=["POST"])
def api_login():
    """Authenticate against the Aplus API and start a Flask session."""
    data = request.json or {}
    mobile = data.get("mobile")
    password = data.get("password")

    if not mobile or not password:
        return jsonify({"error": "Mobile and password are required"}), 400

    try:
        api = AplusAPI()
        token = api.login(mobile, password)
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as-is
        return jsonify({"error": str(exc)}), 401

    session["token"] = token
    session["mobile"] = mobile
    return jsonify({"success": True, "token": token})


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    """Clear the Flask session."""
    session.clear()
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
