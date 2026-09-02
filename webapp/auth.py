"""Login page and login/logout API endpoints."""
import os

from flask import Blueprint, jsonify, render_template, request, session

from player.api import AplusAPI

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    """Render the login page.

    Credentials from the .env file (APLUS_MOBILE / APLUS_PASSWORD, loaded by
    create_app) are passed in so the form can be pre-filled. Whatever the
    user actually submits always takes precedence.
    """
    return render_template("login.html",
                           env_mobile=os.environ.get("APLUS_MOBILE", ""),
                           env_password=os.environ.get("APLUS_PASSWORD", ""))


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
