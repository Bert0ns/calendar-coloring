"""Pure rules that suggest preferences for courses and exams."""

from __future__ import annotations

import hashlib
from collections.abc import Set

from calendar_coloring.events import Enrollment, ExamOccurrence
from calendar_coloring.palette import (
    EXAM_NOT_SUBSCRIBED_COLOR,
    EXAM_SUBSCRIBED_COLOR,
    GoogleColor,
)
from calendar_coloring.preferences import ExamPreference

_COLORS = list(GoogleColor)


def suggest_color(name: str) -> GoogleColor:
    """Deterministic color for a course or deadline name.

    Independent of event processing order, case and surrounding whitespace.
    """
    digest = hashlib.sha256(name.strip().upper().encode("utf-8")).hexdigest()
    return _COLORS[int(digest, 16) % len(_COLORS)]


def default_exam_color(subscribed: bool) -> GoogleColor:
    return EXAM_SUBSCRIBED_COLOR if subscribed else EXAM_NOT_SUBSCRIBED_COLOR


def auto_exam_preference(
    occurrence: ExamOccurrence, subscribed_titles: Set[str]
) -> ExamPreference | None:
    """Preference assigned automatically (non-interactive) to an unseen exam.

    Once subscribed to one session of an exam, every other session of the same
    exam is considered not subscribed. Returns ``None`` when there is no hint.
    """
    if occurrence.title in subscribed_titles:
        return ExamPreference(color=EXAM_NOT_SUBSCRIBED_COLOR, subscribed=False)
    if occurrence.enrollment is Enrollment.NOT_ENROLLED:
        return ExamPreference(color=EXAM_NOT_SUBSCRIBED_COLOR, subscribed=False)
    if occurrence.enrollment is Enrollment.ENROLLED:
        return ExamPreference(color=EXAM_SUBSCRIBED_COLOR, subscribed=True)
    return None


def suggest_exam_subscription(
    occurrence: ExamOccurrence,
    existing: ExamPreference | None,
    subscribed_titles: Set[str],
) -> bool | None:
    """Default answer proposed when asking the user about an exam subscription.

    Returns ``None`` when there is no sensible default.
    """
    if existing is not None:
        return existing.subscribed
    if occurrence.enrollment is Enrollment.ENROLLED:
        return True
    if (
        occurrence.enrollment is Enrollment.NOT_ENROLLED
        or occurrence.title in subscribed_titles
    ):
        return False
    return None


def is_subscribed_to_other_session(
    occurrence: ExamOccurrence,
    existing: ExamPreference | None,
    subscribed_titles: Set[str],
) -> bool:
    """True if the user already subscribed to another session of this exam."""
    return occurrence.title in subscribed_titles and not (
        existing is not None and existing.subscribed
    )
