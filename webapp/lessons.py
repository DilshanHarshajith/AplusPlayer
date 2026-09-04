"""Lesson page, lesson metadata, and playback preparation."""
from flask import Blueprint, jsonify, render_template, request, session

from player.api import AplusAPI
from player.session import PlaybackSession

from . import store
from .decorators import login_required_api, login_required_page

bp = Blueprint("lessons", __name__)


@bp.route("/lesson/<lesson_id>")
@login_required_page
def lesson_page(lesson_id):
    """Render the lesson player page."""
    return render_template("lesson.html", lesson_id=lesson_id)


@bp.route("/api/lesson/<lesson_id>/details")
@login_required_api
def api_lesson_details(lesson_id):
    """Return metadata for a lesson (title, watch time, etc.)."""
    try:
        api = AplusAPI(token=session["token"])
        return jsonify(api.lesson_details(lesson_id))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lesson/<lesson_id>/prepare", methods=["POST"])
@login_required_api
def api_lesson_prepare(lesson_id):
    """Resolve + verify the lesson's video key, ready the proxy can use."""
    user = session.get("mobile", "anonymous")
    try:
        api = AplusAPI(token=session["token"])
        sess = PlaybackSession(api, lesson_id)
        sess.prepare()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    # Small, serializable pointer kept in the Flask session/cookie...
    session["lesson_session"] = {
        "lesson_id": lesson_id,
        "base_url": sess.base_url,
        "video_access_token": sess.video_access_token,
        "playback_hash": sess.playback_hash,
        "video_aes_key": sess.video_aes_key.hex() if sess.video_aes_key else None,
        "segments_encrypted": sess.segments_encrypted,
    }
    # ...while the resolved key data (no live HTTP objects) lives in the
    # shared store, scoped per user so concurrent users never collide and
    # any gunicorn worker can serve the proxy/download paths for it.
    store.put_session(user, lesson_id, sess.to_data().to_dict())

    return jsonify({
        "success": True,
        "base_url": sess.base_url,
        "segments_encrypted": sess.segments_encrypted,
    })


@bp.route("/api/lesson/<lesson_id>/playback-url")
@login_required_api
def api_playback_url(lesson_id):
    """Return the local proxy URL to hand to an HLS player."""
    lesson_session = session.get("lesson_session")
    if not lesson_session or lesson_session.get("lesson_id") != lesson_id:
        return jsonify({"error": "Lesson not prepared"}), 400

    host = request.host.split(":")[0]
    port = request.host.split(":")[1] if ":" in request.host else "5000"
    return jsonify({"playback_url": f"http://{host}:{port}/api/proxy/playback"})


@bp.route("/api/lesson/<lesson_id>/qualities")
@login_required_api
def api_lesson_qualities(lesson_id):
    """Return available quality variants for a lesson."""
    user = session.get("mobile", "anonymous")
    entry = store.get_session(user, lesson_id)
    if not entry:
        return jsonify({"error": "Lesson not prepared"}), 400

    try:
        from player.session import PlaybackSessionData
        data = PlaybackSessionData.from_dict(entry)
        qualities = data.list_qualities()
        return jsonify({"qualities": qualities})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
