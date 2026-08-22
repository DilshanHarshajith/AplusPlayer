"""
Shared in-memory state for the web UI.

The original app.py kept `active_sessions`, `download_progress`, and
`download_cancel` as closures inside `create_app()`, which made them
invisible to anything outside that one function. Pulling them out here
lets every blueprint (lessons, downloads, proxy) share the same state
without importing app.py itself.

This is process-local, in-memory storage — fine for a single dev server
process, but it won't survive a restart and won't work across multiple
worker processes. If this ever needs to run behind gunicorn with more
than one worker, swap this module's dict-backed storage for something
external (Redis, etc.) without touching the blueprints that call it.
"""
import threading

# lesson_id -> {"session": PlaybackSession, "api": AplusAPI}
active_sessions = {}

# lesson_id -> {"status", "progress", "total", "message"}
download_progress = {}

# lesson_id -> bool
download_cancel = {}

_lock = threading.Lock()


def put_session(lesson_id, sess, api):
    """Register a prepared PlaybackSession for later proxy/download use."""
    with _lock:
        active_sessions[lesson_id] = {"session": sess, "api": api}


def get_session(lesson_id):
    """Return the {"session", "api"} entry for a lesson, or None."""
    with _lock:
        return active_sessions.get(lesson_id)


def init_progress(lesson_id):
    """Reset progress tracking for a new download."""
    download_progress[lesson_id] = {
        "status": "preparing",
        "progress": 0,
        "total": 0,
        "message": "Preparing lesson...",
    }
    download_cancel[lesson_id] = False


def update_progress(lesson_id, **fields):
    """Merge fields into a lesson's progress dict, if it exists."""
    entry = download_progress.get(lesson_id)
    if entry is not None:
        entry.update(fields)


def get_progress(lesson_id):
    """Return the progress dict for a lesson, or None if not tracked."""
    return download_progress.get(lesson_id)


def clear_progress(lesson_id):
    """Drop progress tracking for a lesson once it's no longer needed."""
    download_progress.pop(lesson_id, None)
    download_cancel.pop(lesson_id, None)


def is_cancelled(lesson_id):
    """Whether a cancel request has been flagged for this lesson."""
    return download_cancel.get(lesson_id, False)


def set_cancelled(lesson_id):
    """Flag a lesson's in-progress download for cancellation."""
    download_cancel[lesson_id] = True
