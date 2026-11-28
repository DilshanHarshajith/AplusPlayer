"""Lesson page, lesson metadata, and playback preparation."""
import os
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import requests
from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for

from player import config
from player.api import AplusAPI, classify_lesson, is_live_lesson, \
    is_live_stream_lesson, is_video_lesson
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
    # The course page links here with ?course=<id>, so we can send the user
    # straight back to the course they came from. Fall back to the course
    # list when the lesson was opened directly (bookmark, shared link, etc.).
    course_id = request.args.get("course")
    back_url = (
        url_for("courses.course_page", course_id=course_id)
        if course_id
        else url_for("courses.courses_page")
    )
    return render_template(
        "lesson.html", lesson_id=lesson_id, back_url=back_url
    )


@bp.route("/api/lesson/<lesson_id>/details")
@login_required_api
def api_lesson_details(lesson_id):
    """Return metadata for a lesson (title, watch time, etc.)."""
    try:
        api = AplusAPI(token=session["token"])
        det = api.lesson_details(lesson_id)
        lesson = (det.get("lesson") or {})
        # Let the frontend know which viewer to use. LIVE_STREAM lessons have
        # a real (unencrypted) HLS stream; only true Zoom classes need the join
        # panel. Both can carry a nonzero video_can_view, so classify_lesson
        # alone would send the browser down the wrong path.
        link_params = det.get("link_params")
        if is_live_stream_lesson(lesson):
            lesson["content_type"] = "live_stream"
        elif is_live_lesson(lesson):
            lesson["content_type"] = "live"
        else:
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


_ZOOM_RE = re.compile(r"https?://[^\s\"'<>]*zoom\.(?:us|com)[^\s\"'<>]*", re.I)


def _find_zoom(obj):
    """Recursively find the first Zoom URL anywhere in a response."""
    if isinstance(obj, str):
        m = _ZOOM_RE.search(obj)
        return m.group(0) if m else None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_zoom(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_zoom(v)
            if r:
                return r
    return None


def _live_ids(obj, parent_key=""):
    """Collect ids of entries sitting under live/zoom-looking keys."""
    ids = []
    if isinstance(obj, dict):
        if re.search(r"live|zoom", parent_key, re.I):
            for k in ("_id", "id", "live_id"):
                if isinstance(obj.get(k), (str, int)):
                    ids.append(str(obj[k]))
        for k, v in obj.items():
            ids += _live_ids(v, k)
    elif isinstance(obj, list):
        for v in obj:
            ids += _live_ids(v, parent_key)
    return ids


def _next_ids(oid, n=3):
    """Mongo ObjectIds created back-to-back are consecutive: the live entry of
    a ZOOM lesson sits right after the lesson id (e.g. ...5c -> ...5d)."""
    if not isinstance(oid, str) or not re.fullmatch(r"[0-9a-f]{24}", oid):
        return []
    return [format(int(oid, 16) + i, "024x") for i in range(1, n + 1)]


def _trim(obj, n=300):
    if isinstance(obj, str):
        return obj if len(obj) <= n else obj[:n] + "..."
    if isinstance(obj, dict):
        return {k: _trim(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_trim(v, n) for v in obj]
    return obj


def _lesson_selection(api):
    """Ask the vendor schema which fields a Lesson has and build a selection
    set from every scalar (plus one level of nested objects). Returns None
    when introspection is unavailable."""
    tref = "kind name ofType { kind name ofType { kind name ofType { kind name } } }"
    try:
        data = api.gql("query { __schema { queryType { name } types { name kind "
                       "fields { name args { name type { kind } } type { %s } } } } }" % tref, {})
    except Exception:  # noqa: BLE001
        return None
    sch = data["__schema"]
    types = {t["name"]: t for t in sch["types"] if t.get("name")}

    def named(tr):
        while tr and not tr.get("name"):
            tr = tr.get("ofType")
        return tr or {}

    def fields(tname):
        return (types.get(tname) or {}).get("fields") or []

    def usable(f):
        return not any((a.get("type") or {}).get("kind") == "NON_NULL" for a in f.get("args") or [])

    gld = next((f for f in fields(sch["queryType"]["name"]) if f["name"] == "getLessonDetails"), None)
    if not gld:
        return None
    ret = named(gld["type"]).get("name")
    lf = next((f for f in fields(ret) if f["name"] == "lesson"), None)
    if not lf:
        return None
    parts = []
    for f in fields(named(lf["type"]).get("name")):
        if not usable(f):
            continue
        nt = named(f["type"])
        if nt.get("kind") in ("SCALAR", "ENUM"):
            parts.append(f["name"])
        elif nt.get("kind") == "OBJECT":
            sub = [x["name"] for x in fields(nt["name"])
                   if usable(x) and named(x["type"]).get("kind") in ("SCALAR", "ENUM")]
            if sub:
                parts.append("%s { %s }" % (f["name"], " ".join(sub)))
    return " ".join(parts) or None


def _live_content(api, link_params, req_live_id):
    """Same request the official site sends for "Get Link": vid_url only
    (a Zoom lesson has no HLS hash/key to return)."""
    from player import config as _cfg

    q = ("query GetLessonContent($link_param: String, $dev_model: String, $req_live_id: String) "
         "{ getLessonContent(link_param: $link_param, dev_model: $dev_model, "
         "req_live_id: $req_live_id) { vid_url } }")
    return api.gql(q, {"link_param": link_params, "dev_model": _cfg.DEV_MODEL,
                       "req_live_id": req_live_id})["getLessonContent"]


def _live_probe(api, lesson_id):
    """Work out the Zoom join link for a live lesson.

    Mirrors the official site's "Attend Now -> Get Link" button: read the
    lesson's live entries, then ask getLessonContent for each one
    (req_live_id). Any Zoom URL found in a response is returned.
    """
    out = {"zoom_url": None, "attempts": [], "lesson_fields": None}
    sel = _lesson_selection(api)
    out["lesson_fields"] = sel
    det = None
    if sel:
        try:
            det = api.gql("query($id: String){ getLessonDetails(lesson_id: $id) "
                          "{ lesson { %s } link_params } }" % sel, {"id": lesson_id})["getLessonDetails"]
        except Exception as exc:  # noqa: BLE001
            out["details_error"] = str(exc)
    if det is None:
        det = api.lesson_details(lesson_id)
    out["lesson_raw"] = _trim(det)
    out["zoom_url"] = _find_zoom(det)
    link_params = (det or {}).get("link_params")
    if out["zoom_url"] or not link_params:
        return out

    # The official client sends req_live_id = the id of the live entry. Use ids
    # found in the lesson data first, then the ids right after the lesson id.
    lesson = (det or {}).get("lesson") or {}
    cands = list(dict.fromkeys(
        _live_ids(det) + _next_ids(lesson.get("_id")) + [lesson.get("_id") or "", "1", "0"]))
    cands = [c for c in cands if c]
    for i, cand in enumerate(cands[:10]):
        try:
            # link_params is single-use: getLessonContent burns it, so every
            # later guess with the same token would fail with "This lesson
            # link is not valid for your account". Ask for a fresh one each
            # time (the first request can use the token we already have).
            lp = link_params if i == 0 else (api.lesson_details(lesson_id) or {}).get("link_params")
            if not lp:
                out["attempts"].append({"req_live_id": cand, "error": "No lesson link available"})
                continue
            lc = _live_content(api, lp, cand)
            out["attempts"].append({"req_live_id": cand, "vid_url": _trim((lc or {}).get("vid_url"))})
            url = _find_zoom((lc or {}).get("vid_url"))
            if url:
                out["zoom_url"] = url
                break
        except Exception as exc:  # noqa: BLE001
            out["attempts"].append({"req_live_id": cand, "error": str(exc)})
    return out


@bp.route("/api/lesson/<lesson_id>/live-link", methods=["POST"])
@login_required_api
def api_live_link(lesson_id):
    """Return the Zoom join link for a live lesson.

    The vendor sometimes needs a moment (or a second request) before the link
    is ready, so retry a couple of times unless the failure is clearly an
    authentication problem.
    """
    import time as _time
    from flask import current_app

    api = AplusAPI(token=session["token"])
    probe, tries = {}, 0
    for tries in range(1, 4):
        try:
            probe = _live_probe(api, lesson_id)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500
        if probe.get("zoom_url"):
            break
        errs = [a.get("error") or "" for a in probe.get("attempts", []) if a.get("error")]
        if any(re.search(r"unauthor|expired|jwt|token|login", e, re.I) for e in errs):
            break
        # Pure "not valid" answers are deterministic - retrying will not help
        if errs and all(re.search(r"not valid|invalid", e, re.I) for e in errs):
            break
        if tries < 3:
            _time.sleep(1.5)

    errs = [(a.get("req_live_id"), a.get("error")) for a in probe.get("attempts", []) if a.get("error")]
    if probe.get("zoom_url"):
        via = next((a.get("req_live_id") for a in probe["attempts"]
                    if a.get("vid_url") and _find_zoom(a.get("vid_url"))), "details")
        current_app.logger.warning("live-link OK lesson=%s via req_live_id=%r after %d try(s)", lesson_id, via, tries)
        return jsonify({"url": probe["zoom_url"]})

    # Prefer the error from a real live id over the blind guesses
    best = (next((e for rid, e in errs if e and "req_live_id" in e), None)
            or (errs[-1][1] if errs else None))
    current_app.logger.warning("live-link FAILED lesson=%s tries=%d errors=%r", lesson_id, tries, errs)
    msg = ("The vendor server said: " + best) if best else "Could not get the Zoom link automatically."
    return jsonify({"error": msg, "debug": probe}), 502


@bp.route("/api/_diag/live/<lesson_id>")
@login_required_api
def api_diag_live(lesson_id):
    """TEMPORARY: full debug output of the live-link discovery."""
    try:
        return jsonify(_live_probe(AplusAPI(token=session["token"]), lesson_id))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": repr(exc)}), 500


@bp.route("/api/_diag/live-schema")
@login_required_api
def api_diag_live_schema():
    """TEMPORARY: list the vendor GraphQL operations/fields that look
    related to live classes / Zoom, so the real "join live" call can be
    wired up. Open it in the browser while logged in.
    """
    import re as _re

    q = """query Introspect { __schema {
             queryType { name } mutationType { name }
             types { name kind fields { name args { name } } } } }"""
    api = AplusAPI(token=session.get("token", ""))
    try:
        schema = api.gql(q, {})["__schema"]
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "error": repr(exc),
            "hint": "Introspection looks disabled. Capture the request from "
                    "the official site instead (DevTools > Network).",
        }), 500

    pat = _re.compile(r"live|zoom|meet|join|webinar|session|class", _re.I)
    types = {t["name"]: t for t in schema.get("types", []) if t.get("name")}
    roots = {k: (schema.get(k) or {}).get("name") for k in ("queryType", "mutationType")}

    def fields(type_name):
        return [{"name": f["name"], "args": [a["name"] for a in f.get("args") or []]}
                for f in (types.get(type_name) or {}).get("fields") or []]

    out = {"matching_queries": [], "matching_mutations": [], "lesson_like_types": {}}
    for key, label in (("queryType", "matching_queries"), ("mutationType", "matching_mutations")):
        out[label] = [f for f in fields(roots[key]) if pat.search(f["name"])]
    for name, t in types.items():
        if name.startswith("__") or not t.get("fields"):
            continue
        if _re.search(r"lesson|live|zoom", name, _re.I):
            out["lesson_like_types"][name] = [f["name"] for f in t["fields"]]
    return jsonify(out)


@bp.route("/api/lesson/<lesson_id>/files")
@login_required_api
def api_lesson_files(lesson_id):
    """Return metadata for every file attachment on a lesson/card.

    Response: ``{"files": [{"index": 0, "title": "...", "filename": "..."}, ...]}``
    The viewer page calls this up front instead of assuming a card is a
    single file, so multi-file cards (e.g. a PDF plus a Word doc on the
    same card) get a tab per attachment.
    """
    api = AplusAPI(token=session["token"])
    try:
        det = api.lesson_details(lesson_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    lesson = (det or {}).get("lesson") or {}
    if classify_lesson(lesson, det.get("link_params")) == "video":
        return jsonify({"files": []})

    try:
        files = api.resolve_files(lesson_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    payload = []
    for i, entry in enumerate(files):
        payload.append({
            "index": i,
            "title": entry.get("title") or f"File {i + 1}",
            "filename": _suggest_filename(entry["url"], "", f"{lesson_id}_{i}"),
        })
    return jsonify({"files": payload})


@bp.route("/api/lesson/<lesson_id>/file")
@login_required_api
def api_lesson_file(lesson_id):
    """Stream a non-video lesson's file (PDF, document, image, ...).

    Query parameter ``index`` (0-based) selects which attachment in the
    card to serve.  When omitted it defaults to ``0`` (the first file),
    which keeps single-file lessons and legacy callers working unchanged.
    ``?download=1`` forces Content-Disposition: attachment, and
    ``?probe=1`` does a lightweight availability check without streaming
    the body.
    """
    try:
        try:
            file_index = int(request.args.get("index", "0"))
        except (TypeError, ValueError):
            file_index = 0
        if file_index < 0:
            file_index = 0

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
            files = api.resolve_files(lesson_id)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500
        if not files:
            return jsonify({"error": "Could not resolve a file URL for this lesson. "
                                     "The vendor may not support file downloads for it."}), 502
        if file_index >= len(files):
            return jsonify({"error": f"File index {file_index} out of range "
                                     f"(lesson has {len(files)} file(s))."}), 404
        file = files[file_index]

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
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


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
            "color": data.get("color", "#ffffff")
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
