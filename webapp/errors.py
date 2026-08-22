"""404/500 error page handlers."""
from flask import render_template


def register(app):
    """Attach error handlers to the given Flask app."""

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", error="Page not found"), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("error.html", error="Internal server error"), 500
