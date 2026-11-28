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


# Vendor signals for a Zoom/live class. The course listing doesn't mark these
# with a dedicated boolean: the group and/or title says "live", and some
# lessons carry lesson_type "ZOOM".
_LIVE_LABEL_RE = re.compile(r"\blive\b|\bzoom\b", re.I)


def is_live_lesson(lesson: dict | None) -> bool:
    """True when a lesson card represents a Zoom/live class.

    Used to keep live classes in the course menu even when their month isn't
    in ``sub_months``: they are meetings rather than paid video content, so
    hiding them behind the purchase-lock check makes the schedule invisible.
    """
    lesson = lesson or {}
    lesson_type = str(lesson.get("lesson_type") or "").strip().upper()
    # The vendor uses LIVE_STREAM as well as ZOOM/LIVE; substring matching
    # covers both without treating ordinary videos as live classes.
    if "LIVE" in lesson_type or "ZOOM" in lesson_type:
        return True
    return bool(
        _LIVE_LABEL_RE.search(str(lesson.get("group_name") or ""))
        or _LIVE_LABEL_RE.search(str(lesson.get("title") or ""))
    )


def is_live_stream_lesson(lesson: dict | None) -> bool:
    """True for vendor LIVE_STREAM cards, which carry a direct HLS URL.

    Unlike Zoom classes these lessons have a playable (unencrypted) stream and
    no playback key, so they must go through the HLS player, not the join panel.
    """
    lesson_type = str((lesson or {}).get("lesson_type") or "").strip().upper()
    return "LIVE_STREAM" in lesson_type or "LIVESTREAM" in lesson_type


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


def _pad_month(month) -> str:
    """Normalize a lesson/subscription month to 2-digit form ("8" -> "08")."""
    try:
        return f"{int(month):02d}"
    except (TypeError, ValueError):
        return str(month or "01")


def _year_month(year, month) -> str:
    """Build a vendor-style "YYYY_MM" key (zero-padded month) for a lesson."""
    return f"{year}_{_pad_month(month)}"


def _prev_year_month(year, month) -> str:
    """The "YYYY_MM" key one month before (year, month)."""
    y = int(year or 0)
    m = int(_pad_month(month))
    if m == 1:
        return f"{y - 1}_12"
    return f"{y}_{m - 1:02d}"


def _norm_year_month(key) -> str:
    """Normalize a "2025_8"/"2025_08" subscription key to zero-padded form.

    The vendor joins lesson month to year with a raw ``${year}_${month}``
    string, so odd single-digit months would fail a naive exact match even
    though the subscription is real. Normalizing both sides is the safe
    direction: a lesson a normal student can watch is never hidden.
    """
    parts = str(key or "").split("_")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}_{int(parts[1]):02d}"
    return str(key or "")


def lesson_enabled(lesson: dict | None, sub_months: list | None,
                   course_type: str | None) -> bool:
    """Mirror the vendor's lesson-card: is the lesson content enabled?

    The official site renders "Purchase course to enable content" on a
    card when none of these hold; we hide exactly those:

    1. ``is_free`` — free lessons are always available.
    2. Month subscription — for ``MONTHLY_CLASS`` courses the account's
       ``sub_months`` list carries ``year_month`` keys (e.g. "2025_08")
       and a lesson opens when its ``<year>_<month>`` matches. Other
       course types subscribe per lesson, so ``sub_months[].year_month``
       holds the lesson's ``_id`` instead.
    3. ``freeForPrevMonth`` — the lesson is offered free to students who
       paid for the *previous* month.
    """
    lesson = lesson or {}
    if bool(lesson.get("is_free")):
        return True

    sub = sub_months or []
    if course_type == "MONTHLY_CLASS":
        ym = _year_month(lesson.get("year"), lesson.get("month"))
        if any(_norm_year_month(s.get("year_month")) == ym for s in sub):
            return True
    else:
        lesson_id = str(lesson.get("_id") or "")
        if lesson_id and any(str(s.get("year_month")) == lesson_id for s in sub):
            return True

    if lesson.get("freeForPrevMonth"):
        prev = _prev_year_month(lesson.get("year"), lesson.get("month"))
        if any(_norm_year_month(s.get("year_month")) == prev for s in sub):
            return True

    return False


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
        """Return details and lessons for a course.

        ``sub_months`` + ``freeForPrevMonth`` are fetched because they are
        how the official site decides whether a lesson card is content the
        student has access to ("Purchase course to enable content" overlay).
        """
        q = """query GetCourseDetails($course_id: String) {
                 getCourseDetails(course_id: $course_id, paginate_year_months: []) {
                   course { _id title teacher_full_name payment_enabled course_type }
                   lessons { _id title group_name month year is_free freeForPrevMonth
                             created_at lesson_type video_can_view }
                   lessons_months { year month year_month lessons }
                   sub_months { year month year_month }
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

    def _resolve_attachments(self, lesson: dict) -> list[dict]:
        """Extract all valid file attachments from a lesson dict.

        Returns a list of ``{"title": <str>, "url": <str>,
        "headers": <dict|None>, "drive_preview": <str|None>}``
        objects — one per attachment whose ``content`` is a usable
        ``https://`` URL.  Google Drive share links are rewritten to
        their raw-download form.  Attachments whose URL isn't a
        usable ``https://`` link are silently skipped.
        """
        results = []
        for att in (lesson.get("attachments") or []):
            content = (att or {}).get("content")
            if not content:
                continue
            content = str(content).strip()
            if not re.match(r"^https?://", content):
                continue
            entry: dict = {"title": (att or {}).get("title") or "",
                           "url": content, "headers": None}
            drive = google_drive_links(content)
            if drive:
                entry["url"] = drive["download"]
                entry["drive_preview"] = drive["preview"]
            results.append(entry)
        return results

    def resolve_files(self, lesson_id: str) -> list[dict]:
        """Resolve a non-video lesson to ALL fetchable file URLs.

        Returns a (possibly empty) list of file descriptors, each
        ``{"title": <str>, "url": <str>, "headers": <dict|None>,
        "drive_preview": <str|None>}``.

        The vendor stores file URLs in ``Lesson.attachments[].content``
        (hosted on a public CDN like ``drive.apluseducation.lk``), and
        a single lesson/card may have multiple attachments (e.g. a PDF
        plus a Word document).

        Fallbacks, when ``attachments`` yields zero usable entries:

        1. ``link_params`` if it happens to be an http(s) URL.
        2. The ``getLessonContent`` decrypt chain — kept for
           forward-compatibility.
        """
        det = self.lesson_details(lesson_id)
        lesson = (det or {}).get("lesson") or {}
        link_params = (det or {}).get("link_params")

        # Path 1: all vendor attachments with usable https URLs.
        results = self._resolve_attachments(lesson)
        if results:
            return results

        # Path 2: link_params is occasionally itself the file URL
        # (e.g. legacy lessons).
        if link_params and re.match(r"^https?://", str(link_params).strip()):
            return [{"title": "", "url": link_params.strip(),
                     "headers": None}]

        # Path 3: the video-content decrypt chain (rare).
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
                    return [{"title": "", "url": vid_url, "headers": None}]
            except (RuntimeError, requests.RequestException,
                    KeyError, TypeError, ValueError):
                pass

        return []

    def resolve_file(self, lesson_id: str) -> dict | None:
        """Resolve a non-video lesson to a single fetchable file URL.

        Returns ``{"url": <absolute url>, "headers": <dict> | None}``
        for the *first* file attachment, or ``None`` if the lesson is
        a video / has no usable attachments.  Kept for callers that
        only need one file (e.g. the diagnostic endpoint).  For
        multi-file lessons prefer :meth:`resolve_files` instead.
        """
        files = self.resolve_files(lesson_id)
        return files[0] if files else None
