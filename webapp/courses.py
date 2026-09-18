"""Home redirect, course listing, and course-detail pages/API."""
from flask import Blueprint, jsonify, redirect, render_template, session, url_for

from player.api import AplusAPI, lesson_enabled

from .decorators import login_required_api, login_required_page

bp = Blueprint("courses", __name__)


@bp.route("/")
def index():
    """Send authenticated users to their courses, everyone else to login."""
    if "token" not in session:
        return redirect(url_for("auth.login_page"))
    return redirect(url_for("courses.courses_page"))


@bp.route("/courses")
@login_required_page
def courses_page():
    """Render the courses page."""
    return render_template("courses.html")


@bp.route("/api/courses")
@login_required_api
def api_courses():
    """Return the list of courses available to the authenticated student."""
    try:
        api = AplusAPI(token=session["token"])
        return jsonify({"courses": api.my_courses()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/course/<course_id>")
@login_required_page
def course_page(course_id):
    """Render the course-details page."""
    return render_template("course.html", course_id=course_id)


@bp.route("/api/course/<course_id>")
@login_required_api
def api_course_details(course_id):
    """Return details and lessons for a course.

    Lessons the student hasn't paid for (the vendor's "Purchase course to
    enable content" cards) are filtered out so the menu only shows content
    that can actually be watched/downloaded.
    """
    try:
        api = AplusAPI(token=session["token"])
        data = api.course_details(course_id)
        course = data.get("course") or {}
        sub_months = data.get("sub_months") or []
        course_type = course.get("course_type")
        all_lessons = data.get("lessons") or []
        shown_lessons = [
            lesson for lesson in all_lessons
            if lesson_enabled(lesson, sub_months, course_type)
        ]
        data["lessons"] = shown_lessons
        data["hidden_lesson_count"] = len(all_lessons) - len(shown_lessons)
        return jsonify(data)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
