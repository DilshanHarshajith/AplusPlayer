"""GraphQL client: login, course listing, lesson details/content."""
import re
from urllib.parse import urlparse

import requests

from . import config

# Lesson type strings the vendor returns for non-video (file/document) lessons.
# Anything that isn't one of these is treated as a video — matches the
# "videos vs everything else" split the rest of the player assumes.
_NON_VIDEO_LESSON_TYPES = {"pdf", "file", "document", "attachment"}


_GOOGLE_DRIVE_HOSTS = ("drive.google.com", "docs.google.com")


def _google_drive_file_id(url: str) -> str | None:
    """Extract the file ID from a Google Drive share/embed URL.

    The vendor hosts some lesson files on Google Drive. Drive share links
    (``/file/d/<id>/view``, ``/open?id=<id>``, ``/uc?id=<id>``, …) serve
    an HTML app rather than the raw bytes, so before proxying we need to
    pull out the file ID to build the export/preview URLs instead.
    """
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host not in _GOOGLE_DRIVE_HOSTS:
        return None

    # /file/d/<ID>/view|preview and docs.google.com /document/d/<ID>/edit
    m = re.search(r"/(?:file|document|spreadsheets|presentation)/d/([^/?#]+)", u.path)
    if m:
        return m.group(1)

    # /uc?id=<ID> or /open?id=<ID>
    for key, val in re.findall(r"[?&](id)=([^&#]+)", url):
        if key == "id" and val:
            return val
    return None


def google_drive_links(url: str) -> dict | None:
    """For a Google Drive file URL, return the embed + raw-download links.

    Returns ``{"preview": <embeddable url>, "download": <raw bytes url>}``
    or ``None`` if the URL isn't a Drive link. ``preview`` is what you
    load in an iframe to see a clean viewer; ``download`` is the endpoint
    that serves the *actual file bytes* (used when the driver proxies the
    file so the browser renders the PDF directly instead of Google's UI).
    """
    file_id = _google_drive_file_id(url)
    if not file_id:
        return None
    return {
        "preview": f"https://drive.google.com/file/d/{file_id}/preview",
        "download": f"https://drive.google.com/uc?export=download&id={file_id}",
    }


def is_video_lesson(lesson_type) -> bool:
    """Classify a lesson_type string as video (True) or file/document (False).

    Aplus GraphQL returns one of several vendor-specific strings for
    non-video lessons ("pdf", "file", ...). Anything we don't recognise
    is treated as a video — the safer default, since the rest of the
    player is built around the HLS prepare/proxy flow.

    NOTE: this is the *first-pass* classifier on ``lesson_type`` alone.
    Endpoints that need a confident verdict should use ``classify_lesson``
    (which also checks ``video_can_view`` and link_params shape). The
    vendor's ``lesson_type`` is "UPLOAD" for *both* files and videos, so
    this function alone is no longer trustworthy — but it's kept as a
    fast string check for the common case where we only have a lesson
    listing and no ``video_can_view`` field.
    """
    if not lesson_type:
        return True
    return str(lesson_type).strip().lower() not in _NON_VIDEO_LESSON_TYPES


def classify_lesson(lesson: dict | None, link_params: str | None = None) -> str:
    """Multi-signal "video" vs "file" verdict for a lesson.

    Returns ``"video"`` or ``"file"`` using the vendor's signals in
    ``getLessonDetails``, most authoritative first:

    1. ``video_can_view`` — the *real* signal. It's an integer: the
       watch time remaining for the lesson in minutes. ``<= 0`` means
       the student can't watch it as a video — for file lessons (PDFs,
       documents) this is ``0``; for real videos it's positive (e.g.
       10080 = 7 days). Lessons with no value at all default to 0 and
       are treated as files, so the file fast-path doesn't depend on
       the string signals below.
    2. ``lesson_type`` in the known non-video set ("pdf", "file",
       "document", "attachment"). NOTE: the vendor usually returns
       "UPLOAD" for *both* files and videos, so this alone is not
       enough — hence ``video_can_view`` above taking priority.
    3. ``link_params`` shape — when it's already an http(s) URL it's
       almost certainly the file itself, not a tokenised video stream
       reference.

    ``video_can_view`` is watch-time *remaining*, so a genuinely
    paywalled/watched-out video would also carry 0 — but ``resolve_file``
    treats that as "still a video, just not watchable", so the friendly
    "Watch time exceeded" error path is preserved. Unknown ``lesson_type``
    strings default to "video" only when ``video_can_view`` is positive.
    """
    lesson = lesson or {}

    # Signal 1 (authoritative): watch-time remaining in minutes.
    try:
        remaining = int(lesson.get("video_can_view") or 0)
    except (TypeError, ValueError):
        remaining = 0
    if remaining <= 0:
        return "file"

    # Signal 2: lesson_type in the known non-video set.
    if not is_video_lesson(lesson.get("lesson_type")):
        return "file"

    # Signal 3: link_params already looks like an http(s) URL → almost
    # certainly the file itself, not a tokenised video stream reference.
    if link_params and re.match(r"^https?://", str(link_params).strip()):
        return "file"

    return "video"


class AplusAPI:
    """Client for the Aplus GraphQL API."""

    def __init__(self, token: str = ""):
        self.token = token
        self.http = requests.Session()
        self.http.headers["User-Agent"] = config.UA

    def gql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL query and return its data payload."""
        headers = {"Content-Type": "application/json",
                   "authorization": self.token or ""}
        r = self.http.post(config.GQL, json={"query": query, "variables": variables},
                           headers=headers, timeout=90)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            msg = body["errors"][0].get("message", "unknown error")
            # Friendly handling of the vendor's business rules (same toasts
            # the official renderer shows)
            friendly = {
                "Watch time exceeded":
                    "Your plan's watch time for this lesson is used up "
                    "(server-side limit, same as the official app).",
                "link not valid":
                    "This lesson link is not valid for your account.",
                "Please verify student nic before buy lessons":
                    "Verify your NIC with the vendor before accessing "
                    "purchased lessons.",
                "Already attended to physical lesson":
                    "Physical-class attendance restriction applies.",
            }
            for key, text in friendly.items():
                if key in msg:
                    raise RuntimeError(text)
            raise RuntimeError(msg)
        return body.get("data") or {}

    def login(self, mobile: str, password: str) -> str:
        """Authenticate a student and store the returned access token."""
        # renderer normalizes: '+94' + last 9 digits
        digits = re.sub(r"\D", "", mobile)
        normalized = f"+94{digits[-9:]}"
        data = self.gql(
            """mutation loginStudent($input: UserLogIn) {
                 loginStudent(input: $input) { accessToken }
               }""",
            {"input": {"mobile": normalized, "password": password}},
        )
        token = data["loginStudent"]["accessToken"]
        self.token = token
        return token

    def my_courses(self) -> list:
        """Return the courses available to the authenticated student."""
        q = """query MyCourses {
                 myCourses { myCourses { _id title teacher_full_name
                                        subject exm_year status course_type } }
               }"""
        return self.gql(q, {})["myCourses"]["myCourses"]

    def course_details(self, course_id: str) -> dict:
        """Return details and lessons for a course."""
        q = """query GetCourseDetails($course_id: String) {
                 getCourseDetails(course_id: $course_id, paginate_year_months: []) {
                   course { _id title teacher_full_name payment_enabled }
                   lessons { _id title group_name month year is_free created_at lesson_type
                             video_can_view }
                   lessons_months { year month year_month lessons }
                 }
               }"""
        return self.gql(q, {"course_id": course_id})["getCourseDetails"]

    def lesson_details(self, lesson_id: str) -> dict:
        """Return metadata needed to request a lesson's stream.

        ``attachments`` is queried because the vendor stores the actual
        file URL for non-video lessons there (in ``content``), and we
        resolve PDFs / documents straight from it instead of round-
        tripping through ``getLessonContent`` (which returns "Watch
        time exceeded" for file lessons and gives us nothing useful).
        """
        q = """query GetLessonDetails($id: String) {
                 getLessonDetails(lesson_id: $id) {
                   lesson { _id title video_can_view lesson_type
                            attachments { title content } }
                   link_params
                 }
               }"""
        return self.gql(q, {"id": lesson_id})["getLessonDetails"]

    def lesson_content(self, link_param, req_live_id=None) -> dict:
        """Return the encrypted playback metadata for a lesson."""
        q = """query GetLessonContent($link_param: String, $dev_model: String,
                                      $req_live_id: String) {
                 getLessonContent(link_param: $link_param, dev_model: $dev_model,
                                  req_live_id: $req_live_id) {
                   vid_url hash key view_mode
                 }
               }"""
        return self.gql(q, {"link_param": link_param, "dev_model": config.DEV_MODEL,
                            "req_live_id": req_live_id})["getLessonContent"]

    @staticmethod
    def _looks_like_file_url(url: str) -> bool:
        """Best-effort check that a URL points at a static file, not an
        HLS master/variant playlist.

        The vendor serves video via ``….m3u8`` playlists and tokenised
        ``/playback``-ish paths; a direct lesson file (PDF, image, doc)
        is served with an extension and/or none of those markers. This
        is a *content-based* fallback for the case where `lesson_type`
        uses a string we haven't seen and `link_params` isn't itself a
        URL — the classifier's first-pass "video" verdict gets
        overridden when the payload clearly isn't a stream.
        """
        path = urlparse(url).path.lower()
        if "/playback" in path or path.endswith(".m3u8"):
            return False
        # Extension on the path, excluding .ts (stream segment).
        if re.search(r"\.[a-z0-9]{2,8}$", path):
            ext = path.rsplit(".", 1)[-1]
            if ext != "ts":
                return True
        return False

    def resolve_file(self, lesson_id: str) -> dict | None:
        """Resolve a non-video lesson to a fetchable file URL.

        Returns ``{"url": <absolute url>, "headers": <dict> | None}``
        for file/document lessons (PDFs, images, etc.), or ``None`` if
        the lesson is a video (caller should use ``PlaybackSession``
        instead).

        For file lessons the vendor returns the actual file URL in
        ``Lesson.attachments[].content`` (hosted on a public CDN like
        ``drive.apluseducation.lk``), so that's the primary path —
        it works without any auth header and without ever touching
        ``getLessonContent`` (which returns "Watch time exceeded" for
        file lessons, so the older ``vid_url``-based path is dead).

        Fallbacks, in order:

        1. ``lesson.attachments[].content`` — the canonical file URL.
        2. ``link_params`` if it happens to be an http(s) URL (rare;
           usually it's a tokenised "1234-5678" string).
        3. The ``getLessonContent`` decrypt chain — kept for
           forward-compatibility in case a future lesson type stores
           the URL in the encrypted blob instead of in attachments.
        """
        det = self.lesson_details(lesson_id)
        lesson = (det or {}).get("lesson") or {}
        link_params = (det or {}).get("link_params")

        # Path 1: the vendor's `Lesson.attachments[].content` is the
        # canonical place for the actual file URL. We use the first
        # attachment with a usable https URL. Google Drive share links
        # serve an HTML page rather than raw bytes, so we rewrite them
        # to the raw-download form before returning — the caller will
        # proxy the bytes and the iframe renders a clean PDF.
        for att in (lesson.get("attachments") or []):
            content = (att or {}).get("content")
            if not content:
                continue
            content = str(content).strip()
            if not re.match(r"^https?://", content):
                continue
            drive = google_drive_links(content)
            if drive:
                return {"url": drive["download"], "headers": None,
                        "drive_preview": drive["preview"]}
            return {"url": content, "headers": None}

        # Path 2: link_params is occasionally itself the file URL
        # (e.g. legacy lessons). It usually isn't (it's a "1234-5678"
        # token), so this is a thin fallback.
        if link_params and re.match(r"^https?://", str(link_params).strip()):
            return {"url": link_params.strip(), "headers": None}

        # Path 3: the video-content decrypt chain. This returns
        # "Watch time exceeded" for any lesson where video_can_view <= 0
        # (i.e. files), so this path almost never succeeds — but it
        # covers the case where a future lesson type stores the URL in
        # the encrypted blob.
        if link_params and classify_lesson(lesson, link_params) == "file":
            try:
                lc = self.lesson_content(link_params)
                vid_url = (lc or {}).get("vid_url")
                if not vid_url:
                    from .crypto import unwrap_lesson_content
                    crypted, ivs = lc["key"], lc["hash"]
                    meta = unwrap_lesson_content(crypted, ivs, config.KEY_INFO)
                    if meta:
                        vid_url = meta.get("stream_url") or meta.get("vid_url")
                if vid_url and re.match(r"^https?://", vid_url) \
                        and self._looks_like_file_url(vid_url):
                    return {"url": vid_url, "headers": None}
            except (RuntimeError, requests.RequestException,
                    KeyError, TypeError, ValueError):
                # Most file lessons fail here with "Watch time exceeded"
                # — that's expected and not an error to surface.
                pass

        return None
