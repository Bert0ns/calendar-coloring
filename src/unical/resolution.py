"""Automatic (non-interactive) completion of missing preferences."""

from __future__ import annotations

from collections.abc import Iterable

from unical.catalog import Catalog
from unical.events import ExamOccurrence
from unical.preferences import Preferences
from unical.suggestions import auto_exam_preference, suggest_color
from unical.targets import SyncTarget


def fill_missing_course_colors(courses: Iterable[str], prefs: Preferences) -> None:
    for course in courses:
        if prefs.course_color(course) is None:
            prefs.set_course_color(course, suggest_color(course))


def fill_missing_deadline_colors(deadlines: Iterable[str], prefs: Preferences) -> None:
    for deadline in deadlines:
        if prefs.deadline_color(deadline) is None:
            prefs.set_deadline_color(deadline, suggest_color(deadline))


def fill_missing_exam_preferences(
    occurrences: Iterable[ExamOccurrence], prefs: Preferences
) -> None:
    """Assigns automatic preferences to unseen exams, in calendar order.

    Order matters: subscribing to a session makes the following sessions of the
    same exam "not subscribed".
    """
    subscribed_titles = prefs.subscribed_exam_titles()
    for occurrence in occurrences:
        if prefs.exam(occurrence) is not None:
            continue
        preference = auto_exam_preference(occurrence, subscribed_titles)
        if preference is None:
            continue
        prefs.set_exam(occurrence, preference)
        if preference.subscribed:
            subscribed_titles.add(occurrence.title)


def fill_missing_preferences(
    catalog: Catalog, prefs: Preferences, target: SyncTarget
) -> None:
    if target.includes_exams:
        fill_missing_exam_preferences(catalog.exam_occurrences, prefs)
    if target.includes_lectures:
        fill_missing_course_colors(catalog.courses, prefs)
    if target.includes_deadlines:
        fill_missing_deadline_colors(catalog.deadlines, prefs)
