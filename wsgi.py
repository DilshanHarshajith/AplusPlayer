"""WSGI entry point for production servers.

Run with gunicorn (see gunicorn.conf.py):

    gunicorn -c gunicorn.conf.py wsgi:app
"""
from webapp import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
