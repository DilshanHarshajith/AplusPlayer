"""
Flask routes for the local HLS proxy: fetches playlists/segments from the
CDN and hands them to the browser's hls.js player, injecting auth and
rewriting URLs to point back at this proxy.

Playlist decryption and URL-rewriting logic itself lives in
player/streaming.py — this file is just the HTTP plumbing around it,
reading the PlaybackSession that `lessons.api_lesson_prepare` stashed
in `store`.
"""
import requests
from flask import Blueprint, Response, jsonify, request, session

from player import config
from player.streaming import decrypt_if_playlist, rewrite_manifest

from . import store

bp = Blueprint("proxy", __name__, url_prefix="/api/proxy")

_upstream = requests.Session()
_upstream.headers["User-Agent"] = config.UA


def _current_playback_session():
    """Return the PlaybackSession for this browser session's lesson, if any."""
    lesson_session = session.get("lesson_session")
    if not lesson_session:
        return None
    entry = store.get_session(lesson_session.get("lesson_id"))
    return entry["session"] if entry else None


def _origin():
    host = request.host.split(":")[0]
    port = request.host.split(":")[1] if ":" in request.host else "5000"
    return f"http://{host}:{port}"


@bp.route("/playback")
def playback():
    """Serve the (decrypted, rewritten) master playlist."""
    sess = _current_playback_session()
    if sess is None:
        return jsonify({"error": "No active lesson session"}), 400

    try:
        r = _upstream.get(
            sess.base_url + "/playback",
            headers={"Authorization": f"Bearer {sess.video_access_token}"},
            timeout=90)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    if r.status_code != 200:
        return jsonify({"error": f"Upstream error: {r.status_code}"}), 502

    body = r.text.strip()
    decrypted = decrypt_if_playlist(body, sess)
    if decrypted is None:
        return jsonify({"error": "Unexpected playlist format"}), 502

    return Response(rewrite_manifest(decrypted, sess, _origin()),
                    mimetype="application/vnd.apple.mpegurl")


@bp.route("/video.key")
def video_key():
    """Serve the resolved AES video key as raw bytes."""
    sess = _current_playback_session()
    if sess is None:
        return jsonify({"error": "No active lesson session"}), 400
    if not sess.video_aes_key:
        return jsonify({"error": "No video key available"}), 404
    return Response(sess.video_aes_key, mimetype="application/octet-stream")


@bp.route("/<path:path>")
def media(path):
    """Serve variant playlists and media segments, decrypting as needed."""
    sess = _current_playback_session()
    if sess is None:
        return jsonify({"error": "No active lesson session"}), 400

    try:
        r = _upstream.get(
            sess.base_url + "/" + path,
            headers={"Authorization": f"Bearer {sess.video_access_token}"},
            timeout=120)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    if r.status_code != 200:
        return jsonify({"error": f"Upstream error: {r.status_code}"}), 502

    body = bytes(r.content)
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = None

    if text:
        decrypted = decrypt_if_playlist(text, sess)
        if decrypted is not None:
            return Response(rewrite_manifest(decrypted, sess, _origin()),
                            mimetype="application/vnd.apple.mpegurl")

    return Response(body, mimetype=r.headers.get("Content-Type",
                                                  "application/octet-stream"))
