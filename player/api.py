"""GraphQL client: login, course listing, lesson details/content."""
import re

import requests

from . import config


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
                   lessons { _id title group_name month year is_free created_at }
                   lessons_months { year month year_month lessons }
                 }
               }"""
        return self.gql(q, {"course_id": course_id})["getCourseDetails"]

    def lesson_details(self, lesson_id: str) -> dict:
        """Return metadata needed to request a lesson's stream."""
        q = """query GetLessonDetails($id: String) {
                 getLessonDetails(lesson_id: $id) {
                   lesson { _id title video_can_view lesson_type }
                   link_params
                   user_watch_time
                 }
               }"""
        return self.gql(q, {"id": lesson_id})["getLessonDetails"]

    def lesson_content(self, link_param, req_live_id=None) -> dict:
        """Return the encrypted playback metadata for a lesson."""
        q = """query GetLessonContent($link_param: String, $dev_model: String,
                                      $req_live_id: String) {
                 getLessonContent(link_param: $link_param, dev_model: $dev_model,
                                  req_live_id: $req_live_id) {
                   vid_url hash key user_watch_time view_mode
                 }
               }"""
        return self.gql(q, {"link_param": link_param, "dev_model": config.DEV_MODEL,
                            "req_live_id": req_live_id})["getLessonContent"]
