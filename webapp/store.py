"""
Shared cross-worker state for the web UI.

Multi-user / multi-worker notes
-------------------------------
The original store kept three process-local dicts keyed by lesson_id alone.
That broke two ways for a shared web service: (1) two users watching the
*same* lesson clobbered each other's session and download progress, and
(2) the state lived only in one process, so gunicorn's multiple workers
couldn't see it and every restart wiped it.

This module replaces those dicts with a key-value store that is:

  * **user-scoped** — every key is namespaced by ``<namespace>:<user>:<lesson>``,
    so different users never collide, even on the same lesson.
  * **worker-safe** — backed by a single SQLite file in WAL mode with a busy
    timeout, so every gunicorn worker (and the browser's progress polls) reads
    and writes the same state.
  * **durable** — the DB file survives restarts and can be mounted on a
    persistent volume (see docker-compose).

Namespaces::

    session:<user>:<lesson>    -> JSON: serializable playback-session snapshot
    progress:<user>:<lesson>   -> JSON: download progress dict
    cancel:<user>:<lesson>     -> "1"/"0": download-cancel flag

The callers (lessons, downloads, proxy blueprints) pass the logged-in user
(``session["mobile"]``) explicitly; this module never touches Flask.
"""
import json
import os
import sqlite3
import time

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aplus_state.db",
)
# Overridable via env so Docker can point it at a mounted volume.
STORE_PATH = os.environ.get("APLUS_STATE_DB", _DEFAULT_DB)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _connect():
    """Open a SQLite connection with multi-worker-friendly pragmas."""
    directory = os.path.dirname(STORE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(STORE_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _init_schema():
    conn = _connect()
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _set(key: str, value: str):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value=excluded.value, updated_at=excluded.updated_at",
            (key, value, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _get(key: str):
    conn = _connect()
    try:
        cur = conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _delete(key: str):
    conn = _connect()
    try:
        conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def _key(namespace: str, user: str, lesson_id: str) -> str:
    """Build a user-scoped storage key (never just the lesson id)."""
    return f"{namespace}:{user}:{lesson_id}"


# ---------------------------------------------------------------------------
# Playback-session snapshots
# ---------------------------------------------------------------------------

def put_session(user, lesson_id, data: dict):
    """Store a serializable playback-session snapshot (a to_dict() payload)."""
    _set(_key("session", user, lesson_id), json.dumps(data))


def get_session(user, lesson_id):
    """Return the stored session snapshot (dict) for a user+lesson, or None."""
    raw = _get(_key("session", user, lesson_id))
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Download progress + cancellation
# ---------------------------------------------------------------------------

def init_progress(user, lesson_id):
    """Reset progress tracking for a new download by this user."""
    _set(_key("progress", user, lesson_id), json.dumps({
        "status": "preparing",
        "progress": 0,
        "total": 0,
        "message": "Preparing lesson...",
    }))
    _set(_key("cancel", user, lesson_id), "0")


def update_progress(user, lesson_id, **fields):
    """Merge fields into a lesson's progress dict, if it exists."""
    raw = _get(_key("progress", user, lesson_id))
    if raw is None:
        return
    entry = json.loads(raw)
    entry.update(fields)
    _set(_key("progress", user, lesson_id), json.dumps(entry))


def get_progress(user, lesson_id):
    """Return the progress dict for a user+lesson, or None if not tracked."""
    raw = _get(_key("progress", user, lesson_id))
    return json.loads(raw) if raw else None


def clear_progress(user, lesson_id):
    """Drop progress + cancel tracking once a download is done."""
    _delete(_key("progress", user, lesson_id))
    _delete(_key("cancel", user, lesson_id))


def is_cancelled(user, lesson_id) -> bool:
    """Whether a cancel request has been flagged for this user+lesson."""
    return _get(_key("cancel", user, lesson_id)) == "1"


def set_cancelled(user, lesson_id):
    """Flag a user's in-progress download for cancellation."""
    _set(_key("cancel", user, lesson_id), "1")


# Ensure the schema exists the first time this module is imported.
_init_schema()
