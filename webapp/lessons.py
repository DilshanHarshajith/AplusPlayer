"""Lesson page, lesson metadata, and playback preparation."""
import os
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import requests
from flask import Blueprint, Response, jsonify, redirect, render_template, request, session

from player import config
from player.api import AplusAPI, classify_lesson, is_video_lesson
from player.session import PlaybackSession

from . import store
from .decorators import login_required_api, login_required_page

bp = Blueprint("lessons", __name__)

# Shared upstream session for fetching lesson files from the vendor CDN.
_upstream = requests.Session()
_upstream.headers["User-Agent"] = config.UA


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
        det = api.lesson_details(lesson_id)
        lesson = (det.get("lesson") or {})
        # Let the frontend know whether this lesson is a video or a plain
        # file/document (PDF etc.) so it can render the right viewer.
        # classify_lesson uses several signals (lesson_type, video_can_view,
        # link_params shape) so we don't have to guess a single vendor
        # string for "this is a PDF".
        link_params = det.get("link_params")
        lesson["content_type"] = classify_lesson(lesson, link_params)
        return jsonify(det)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/_diag/lesson/<lesson_id>")
@login_required_api
def api_diag_lesson(lesson_id):
    """TEMPORARY: dump the raw vendor responses for a lesson.

    Used to calibrate file-vs-video detection. Remove once the real
    lesson_type / content values for PDF lessons are known.
    """
    import json as _json

    api = AplusAPI(token=session.get("token", ""))
    out = {"lesson_id": lesson_id,
           "session_keys": list(session.keys())}

    # 1. Direct lesson details (has lesson_type + video_can_view).
    try:
        det = api.lesson_details(lesson_id)
        out["details"] = det
        out["details_lesson_keys"] = list((det or {}).get("lesson", {}).keys()) if det else None
    except Exception as exc:  # noqa: BLE001
        out["details_error"] = repr(exc)

    # 2. Lesson content (has vid_url/key/hash — useful for seeing if
    #    the vendor treats this as a video stream at all).
    lp = (out.get("details") or {}).get("link_params")
    out["link_params_raw"] = lp
    if lp:
        try:
            out["content"] = api.lesson_content(lp)
            c = out["content"] or {}
            out["content_keys"] = list(c.keys())
            out["vid_url"] = c.get("vid_url")
            out["view_mode"] = c.get("view_mode")
            out["hash_is_str"] = isinstance(c.get("hash"), str)
            out["hash_preview"] = (c.get("hash") or "")[:80] if isinstance(c.get("hash"), str) else None
            out["key_is_str"] = isinstance(c.get("key"), str)
            out["key_preview"] = (c.get("key") or "")[:80] if isinstance(c.get("key"), str) else None
        except Exception as exc:  # noqa: BLE001
            out["content_error"] = repr(exc)

    # 3. resolve_file — does it manage to surface a usable URL for this lesson?
    try:
        out["resolve_file"] = api.resolve_file(lesson_id)
    except Exception as exc:  # noqa: BLE001
        out["resolve_file_error"] = repr(exc)

    # 4. is_video_lesson verdict on whatever lesson_type the vendor returned.
    lesson = (out.get("details") or {}).get("lesson") or {}
    out["lesson_type_value"] = lesson.get("lesson_type")
    out["video_can_view"] = lesson.get("video_can_view")
    out["is_video_lesson_verdict"] = is_video_lesson(lesson.get("lesson_type"))
    out["classify_lesson_verdict"] = classify_lesson(lesson, out.get("link_params_raw"))

    _json.dump(out, open("/tmp/aplus_diag.json", "w"), indent=2, default=str)
    return jsonify(out)


@bp.route("/api/lesson/<lesson_id>/file")
@login_required_api
def api_lesson_file(lesson_id):
    """Stream a non-video lesson's file (PDF, document, image, ...)."""
    api = AplusAPI(token=session["token"])
    try:
        det = api.lesson_details(lesson_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    lesson = (det or {}).get("lesson") or {}
    link_params = det.get("link_params")
    if classify_lesson(lesson, link_params) == "video":
        return jsonify({"error": "This lesson is a video, not a file."}), 400

    try:
        file = api.resolve_file(lesson_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    if file is None:
        return jsonify({"error": "Could not resolve a file URL for this lesson. "
                                 "The vendor may not support file downloads for it."}), 502

    url = file["url"]
    headers = file.get("headers")
    try:
        r = _upstream.get(url, headers=headers, stream=True, timeout=120)
    except requests.RequestException as exc:
        return jsonify({"error": f"Failed to reach lesson file: {exc}"}), 502
    if r.status_code != 200:
        return jsonify({"error": f"Lesson file unavailable (upstream {r.status_code})"}), 502

    # The upstream Content-Type for Drive-hosted files is often generic
    # ("application/octet-stream"), so we sniff the first bytes to get the
    # *real* type + extension. If the bytes turn out to be HTML (Google
    # returns an interstitial for large files before the download) we
    # redirect the browser to the embeddable Drive preview instead.
    content_type, ext = sniff_file_type(r, url)

    drive_preview = file.get("drive_preview")
    if _is_html(content_type):
        r.close()
        # ?download=1 wants the file, not a redirect — fall back to the
        # Drive confirmation flow so the browser can complete it.
        if request.args.get("download") == "1":
            return redirect(drive_preview + "?export=download") if drive_preview \
                else jsonify({"error": "Cloud file blocked the download "
                                       "(upstream returned HTML)."}), 502
        # Plain navigation (iframe / new tab): hand the browser the
        # clean Drive viewer.
        if drive_preview:
            return redirect(drive_preview)
        return jsonify({"error": "Cloud file unavailable "
                                 "(upstream returned HTML)."}), 502

    filename = _suggest_filename(url, content_type, lesson_id, ext)
    disposition = "attachment" if request.args.get("download") == "1" else "inline"

    # ?probe=1: light-weight check used by the lesson page when it
    # suspects a lesson is actually a file. Skip the body — we only
    # need to know the upstream returned 200 + a content-type, so
    # downloading a multi-MB PDF just to test isn't useful.
    if request.args.get("probe") == "1":
        r.close()
        return Response(
            status=200,
            mimetype=content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "Cache-Control": "no-cache",
            })

    # The sniff may have consumed the first chunk of the stream; re-yield
    # it first so the file isn't truncated.
    chunk = getattr(r, "_peeked", None)
    if chunk:
        def _chain():
            yield chunk
            for c in r.iter_content(chunk_size=65536):
                yield c
        chunks = _chain()
    else:
        chunks = (c for c in r.iter_content(chunk_size=65536))

    return Response(
        chunks,
        mimetype=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-cache",
        })


# Magic bytes that identify common lesson-file types. The upstream headers
# for Drive-hosted files are often generic, so the extension is best taken
# from the bytes themselves (browsers also need the real Content-Type to
# render a PDF inline instead of showing a blank iframe).
_FILE_MAGIC = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"PK\x03\x04", "application/zip", ".zip"),   # docx / xlsx / pptx
    (b"{\\rtf", "application/rtf", ".rtf"),
)


def sniff_file_type(r, url: str) -> tuple:
    """Return (content_type, extension) for an upstream stream `r`.

    Uses the upstream Content-Type when it's concrete; otherwise sniffs
    the first bytes for magic numbers. The sniff consumes a small chunk,
    which the caller's streaming generator re-yields via `r.iter_content`.
    """
    declared = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    # Trust a concrete declared type that isn't generic.
    if declared and declared != "application/octet-stream" and not _is_html(declared):
        return declared, _ext_for_mime(declared, url)

    # Sniff actual bytes.
    chunk = _peek(r, 512)
    for magic, mime, ext in _FILE_MAGIC:
        if chunk.startswith(magic):
            r._peeked = chunk  # re-yield below
            return mime, ext
    # Drive's HTML interstitial (redirect/cookie page) is a real possibility.
    if chunk.lstrip().lower().startswith(b"<") or b"<!doctype html" in chunk.lower():
        r._peeked = chunk  # re-yield below (we redirect, but harmless)
        return "text/html", ".html"
    # Give up: keep the declared type, no useful extension.
    return declared or "application/octet-stream", ""


def _peek(r, size: int) -> bytes:
    """Read up to `size` bytes from a stream, ready to re-yield on iter."""
    try:
        return next(r.iter_content(size or 512), b"")
    except (requests.RequestException, StopIteration, ValueError):
        return b""


def _is_html(content_type: str) -> bool:
    return (content_type or "").startswith("text/html")


def _ext_for_mime(mime: str, url: str) -> str:
    for _, m, e in _FILE_MAGIC:
        if m == mime:
            return e
    if mime.startswith("image/"):
        return ".img"
    if mime == "application/pdf":
        return ".pdf"
    return _ext_from_url(url)


def _ext_from_url(url: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", urlsplit(url).path)
    return f".{m.group(1)}" if m else ""


def _suggest_filename(url: str, content_type: str, fallback: str, ext: str = "") -> str:
    """Best-effort filename from the URL path or the sniffed extension."""
    path = urlsplit(url).path
    base = PurePosixPath(unquote(path)).name
    if base and re.search(r"\.[A-Za-z0-9]{1,8}$", base):
        return base
    if not ext:
        ext = _ext_for_mime(content_type, url)
    return f"{fallback}{ext}"


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
    """Return available quality variants for a lesson. Prepares it if needed."""
    user = session.get("mobile", "anonymous")
    try:
        from player.session import PlaybackSession, PlaybackSessionData

        entry = store.get_session(user, lesson_id)
        if entry:
            data = PlaybackSessionData.from_dict(entry)
        else:
            api = AplusAPI(token=session["token"])
            sess = PlaybackSession(api, lesson_id)
            sess.prepare()
            store.put_session(user, lesson_id, sess.to_data().to_dict())
            data = sess.to_data()

        qualities = data.list_qualities()
        return jsonify({"qualities": qualities})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lesson/<lesson_id>/timestamps", methods=["GET"])
@login_required_api
def api_get_timestamps(lesson_id):
    """Return all timestamps for a lesson."""
    user = session.get("mobile", "anonymous")
    try:
        timestamps = store.get_timestamps(user, lesson_id)
        return jsonify({"timestamps": timestamps})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lesson/<lesson_id>/timestamps", methods=["POST"])
@login_required_api
def api_add_timestamp(lesson_id):
    """Add a new timestamp to a lesson."""
    user = session.get("mobile", "anonymous")
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Validate required fields
        if "time" not in data:
            return jsonify({"error": "Missing required field: time"}), 400
        
        timestamp = {
            "time": float(data["time"]),
            "note": data.get("note", ""),
            "color": data.get("color", "#7ab")
        }
        
        store.add_timestamp(user, lesson_id, timestamp)
        timestamps = store.get_timestamps(user, lesson_id)
        return jsonify({"success": True, "timestamps": timestamps})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lesson/<lesson_id>/timestamps/<timestamp_id>", methods=["PUT"])
@login_required_api
def api_update_timestamp(lesson_id, timestamp_id):
    """Update an existing timestamp."""
    user = session.get("mobile", "anonymous")
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        updated = store.update_timestamp(user, lesson_id, timestamp_id, data)
        if not updated:
            return jsonify({"error": "Timestamp not found"}), 404
        
        timestamps = store.get_timestamps(user, lesson_id)
        return jsonify({"success": True, "timestamps": timestamps})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lesson/<lesson_id>/timestamps/<timestamp_id>", methods=["DELETE"])
@login_required_api
def api_delete_timestamp(lesson_id, timestamp_id):
    """Delete a timestamp."""
    user = session.get("mobile", "anonymous")
    try:
        deleted = store.delete_timestamp(user, lesson_id, timestamp_id)
        if not deleted:
            return jsonify({"error": "Timestamp not found"}), 404
        
        timestamps = store.get_timestamps(user, lesson_id)
        return jsonify({"success": True, "timestamps": timestamps})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
