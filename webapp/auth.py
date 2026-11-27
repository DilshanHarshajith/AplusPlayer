"""Login page and login/logout API endpoints."""
import os

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)

from player.api import AplusAPI

bp = Blueprint("auth", __name__)

_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)


def _env_file_credentials():
    """Read the two credential keys from the project .env (no extra deps).

    Docker Compose already passes these through as environment variables; this
    also makes a plain `python3 AplusPlayer.py` run pick them up. Only these
    keys are read, and values in the real environment take precedence.
    """
    values = {}
    try:
        with open(_ENV_FILE, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, separator, value = line.partition("=")
                key = key.strip()
                if not separator or key not in ("APLUS_MOBILE", "APLUS_PASSWORD"):
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                values[key] = value
    except OSError:
        pass
    return values


def _saved_credentials():
    """Return optional credentials for a private, single-user deployment.

    Both APLUS_MOBILE and APLUS_PASSWORD must be present in the environment or
    .env; otherwise only the normal per-user login form is available.
    """
    file_values = _env_file_credentials()
    mobile = os.environ.get("APLUS_MOBILE", file_values.get("APLUS_MOBILE", "")).strip()
    password = os.environ.get("APLUS_PASSWORD", file_values.get("APLUS_PASSWORD", ""))
    if mobile and password:
        return mobile, password
    return None


def _start_session(mobile, password):
    """Authenticate against Aplus and store the token in this browser session."""
    token = AplusAPI().login(mobile, password)
    session["token"] = token
    session["mobile"] = mobile
    session.pop("skip_autologin", None)


def _render_login(auto_login_error=None):
    """Render the form, advertising the saved account when one is configured."""
    creds = _saved_credentials()
    return render_template(
        "login.html",
        saved_mobile=creds[0] if creds else None,
        auto_login_error=auto_login_error,
    )


@bp.route("/login")
def login_page():
    """Auto-login from saved credentials, or render the login form.

    A manual logout is respected: the form is shown again instead of silently
    signing back in, but the saved account stays available as a one-click
    option. Any successful login re-enables auto-login next time.
    """
    creds = _saved_credentials()
    if creds and "token" not in session and not session.get("skip_autologin"):
        try:
            _start_session(*creds)
        except Exception as exc:  # noqa: BLE001 - show the reason on the form
            return _render_login(auto_login_error=f"Saved login failed: {exc}")
        return redirect(url_for("courses.courses_page"))
    return _render_login()


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


@bp.route("/api/login/saved", methods=["POST"])
def api_saved_login():
    """Log in with the .env credentials — still available after a logout."""
    creds = _saved_credentials()
    if not creds:
        return jsonify({"error": "No saved credentials are configured"}), 400

    try:
        _start_session(*creds)
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as-is
        return jsonify({"error": f"Saved login failed: {exc}"}), 401

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
