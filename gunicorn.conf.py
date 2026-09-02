"""Gunicorn configuration for the Aplus Player web service.

Most values are overridable via environment variables so the same config
works on a laptop and in Docker without edits.
"""
import multiprocessing
import os

# Bind to all interfaces inside the container; a reverse proxy in front is
# recommended for TLS and to shield the app from the public network.
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")

# A handful of worker processes; each runs its own event loop. Downloads are
# IO-bound (segment fetch + decrypt) so they block only their own thread.
workers = int(os.environ.get("GUNICORN_WORKERS", max(2, multiprocessing.cpu_count())))

# Threads per worker (gthread) so concurrent proxy/API requests aren't
# serialized behind a single blocking request.
threads = int(os.environ.get("GUNICORN_THREADS", 8))
worker_class = "gthread"

# Downloads stream for a long time; keep the timeout generous so a slow
# connection isn't killed mid-transfer.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 600))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", 30))
keepalive = 5

accesslog = os.environ.get("GUNICORN_ACCESSLOG", "-")
errorlog = os.environ.get("GUNICORN_ERRORLOG", "-")
