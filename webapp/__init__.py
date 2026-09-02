"""
Web UI package for Aplus Player — everything HTTP-facing lives here: page
routes, JSON API routes, streaming download responses, and the HLS proxy.
All of them are Flask blueprints, and all of them call into the sibling
`player/` package for the actual site/API interaction and lesson
processing (login, GraphQL calls, decryption). Nothing in `player/`
imports Flask.

  auth.py        login page, login/logout API
  courses.py     home redirect, course listing, course details
  lessons.py     lesson page, lesson details, playback prepare
  downloads.py   streamed lesson download + progress/cancel API
  proxy.py       HLS playlist/segment proxy for the browser player
  store.py       shared in-memory session + download-progress state
  decorators.py  login_required_page / login_required_api guards
  errors.py      404/500 handlers
"""
import os

from flask import Flask


def _load_dotenv(path):
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Real environment variables always win over the file: a key already set
    in `os.environ` is left untouched, so secrets in the shell aren't
    clobbered by a stale .env. Lines are `KEY=VALUE`; blank lines, `#`
    comments, and malformed lines are skipped, and surrounding quotes on
    the value are stripped.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"").strip()
            if key and key not in os.environ:
                os.environ[key] = value


def create_app():
    """Create and configure the Flask application."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    _load_dotenv(os.path.join(base_dir, "..", ".env"))

    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY",
                                    "dev-secret-key-change-in-production")

    app.template_folder = os.path.join(base_dir, "templates")
    app.static_folder = os.path.join(base_dir, "static")

    from . import auth, courses, downloads, errors, lessons, proxy

    app.register_blueprint(auth.bp)
    app.register_blueprint(courses.bp)
    app.register_blueprint(lessons.bp)
    app.register_blueprint(downloads.bp)
    app.register_blueprint(proxy.bp)
    errors.register(app)

    return app


__all__ = ["create_app"]
