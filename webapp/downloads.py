"""
Flask routes for downloading a lesson: streams a decrypted, optionally
ffmpeg-remuxed video file back to the browser while progress is tracked
in `store` for the polling `/download/progress` endpoint to read.

The actual segment fetch/decrypt/remux work happens in
player/download_engine.py — this file is just the HTTP streaming,
progress reporting, and cancellation plumbing around it.
"""
import os
import shutil
import tempfile
import time

from flask import Blueprint, Response, jsonify, request, session

from player.api import AplusAPI, classify_lesson
from player.download_engine import download_and_remux
from player.session import PlaybackSession

from . import store
from .decorators import login_required_api

bp = Blueprint("downloads", __name__)


def _resolve_session(user, lesson_id, api):
    """Reuse an already-prepared snapshot for this user+lesson if one exists,
    otherwise prepare a fresh one (e.g. downloading straight from the
    course list without having opened the lesson player first).

    Returns a PlaybackSessionData, which carries everything the download
    engine needs (base URL, bearer token, AES key, playlist hash) without
    any live AplusAPI object — safe to rebuild in whichever worker serves
    this request.
    """
    from player.session import PlaybackSessionData

    entry = store.get_session(user, lesson_id)
    if entry is not None:
        return PlaybackSessionData.from_dict(entry)
    sess = PlaybackSession(api, lesson_id)
    sess.prepare()
    store.put_session(user, lesson_id, sess.to_data().to_dict())
    return sess.to_data()


@bp.route("/api/lesson/<lesson_id>/download", methods=["POST"])
@login_required_api
def api_lesson_download(lesson_id):
    """Stream a decrypted, optionally remuxed video file to the client."""
    user = session.get("mobile", "anonymous")
    store.init_progress(user, lesson_id)

    try:
        # Get quality parameter from request (silent: body may be absent,
        # e.g. the course page's download buttons post no JSON at all)
        request_data = request.get_json(silent=True) or {}
        quality = request_data.get("quality", "auto")

        api = AplusAPI(token=session["token"])

        # Bail early for non-video lessons: PDFs and similar files are
        # served via /api/lesson/<id>/file (with ?download=1) — trying
        # to push them through the HLS segment path yields garbage.
        det = api.lesson_details(lesson_id)
        lesson = (det.get("lesson") or {})
        if classify_lesson(lesson, det.get("link_params")) != "video":
            store.update_progress(user, lesson_id, status="error",
                                  message="This lesson is a file (e.g. PDF), not a video. Use the file viewer download button.")
            return jsonify({"error": "This lesson is a file, not a video. "
                                     "Use the file viewer to download it."}), 400

        data = _resolve_session(user, lesson_id, api)
        seg_paths, iv = data.list_variant_segments(quality=quality)
    except Exception as exc:  # noqa: BLE001
        store.update_progress(user, lesson_id, status="error", message=str(exc))
        return jsonify({"error": str(exc)}), 500

    total = len(seg_paths)
    store.update_progress(user, lesson_id, status="downloading", progress=5,
                          total=total, message="Downloading segments...")

    temp_dir = tempfile.gettempdir()
    temp_ts = os.path.join(temp_dir, f"aplus_download_{lesson_id}.ts")
    temp_mp4 = os.path.join(temp_dir, f"aplus_download_{lesson_id}.mp4")
    ffmpeg_available = shutil.which("ffmpeg") is not None
    content_type = "video/mp4" if ffmpeg_available else "video/mp2t"
    file_extension = ".mp4" if ffmpeg_available else ".ts"

    def cleanup():
        for path in (temp_ts, temp_mp4):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def generate():
        try:
            final_file = download_and_remux(
                data, seg_paths, iv, temp_ts, temp_mp4,
                is_cancelled=lambda: store.is_cancelled(user, lesson_id),
                on_progress=lambda **fields: store.update_progress(user, lesson_id, **fields))

            if final_file is None:
                # Cancelled mid-download. Stop quietly rather than raising:
                # the response already started streaming (status 200 is
                # long since sent), so an exception here can't turn into
                # an error response for the client anyway — it would only
                # spam the server log with a traceback for what is normal,
                # user-requested behavior. Returning just closes the
                # (now-truncated, discarded) download stream.
                store.update_progress(user, lesson_id, status="cancelled",
                                      message="Download cancelled")
                cleanup()
                store.clear_progress(user, lesson_id)
                return

            store.update_progress(user, lesson_id, status="sending", progress=99,
                                  message="Transferring file...")

            with open(final_file, "rb") as fh:
                while True:
                    chunk = fh.read(8192)
                    if not chunk:
                        break
                    yield chunk

            store.update_progress(user, lesson_id, status="completed", progress=100,
                                  message="Download completed")
            cleanup()
            # give the client's final poll a moment to see "completed"
            # before the entry disappears
            time.sleep(5)
            store.clear_progress(user, lesson_id)
        except Exception as exc:  # noqa: BLE001
            store.update_progress(user, lesson_id, status="error",
                                  message=f"Download failed: {exc}")
            cleanup()
            raise

    return Response(
        generate(),
        mimetype=content_type,
        headers={
            "Content-Disposition": f"attachment; filename={lesson_id}{file_extension}",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })


@bp.route("/api/lesson/<lesson_id>/download/progress")
@login_required_api
def api_download_progress(lesson_id):
    """Poll the current progress of a lesson's in-flight download."""
    user = session.get("mobile", "anonymous")
    progress = store.get_progress(user, lesson_id)
    if progress is None:
        return jsonify({"status": "not_started", "progress": 0,
                        "message": "Download not started"})
    return jsonify(progress)


@bp.route("/api/lesson/<lesson_id>/download/cancel", methods=["POST"])
@login_required_api
def api_download_cancel(lesson_id):
    """Flag an in-flight download for cancellation."""
    user = session.get("mobile", "anonymous")
    if store.get_progress(user, lesson_id) is None:
        return jsonify({"error": "No active download to cancel"}), 404
    store.set_cancelled(user, lesson_id)
    store.update_progress(user, lesson_id, status="cancelled",
                          message="Download cancelled")
    return jsonify({"success": True, "message": "Download cancelled"})
