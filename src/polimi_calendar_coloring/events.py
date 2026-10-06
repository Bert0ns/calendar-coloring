"""Parsing of Polimi calendar events into domain concepts.

This is the only module that knows how Polimi formats event titles,
descriptions and iCal categories.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

Event = dict[str, Any]
"""A raw Google Calendar event resource."""

LECTURE_PREFIX = "Lezione: Didattica - "
EXAM_PREFIX = "Esame: "
DEADLINE_PREFIX = "Scadenza: "

# iCal feeds may omit the title prefixes but tag events with these CATEGORIES.
LECTURE_CATEGORY = "Lezione"
EXAM_CATEGORY = "Esame"
DEADLINE_CATEGORY = "Scadenza"

UNKNOWN_DATE = "Unknown Date"


class Enrollment(Enum):
    """Exam enrollment status as reported by the event description."""

    ENROLLED = "enrolled"
    NOT_ENROLLED = "not_enrolled"
    UNKNOWN = "unknown"


def summary_of(event: Event) -> str:
    return event.get("summary") or ""


def description_of(event: Event) -> str:
    return event.get("description") or ""


def categories_of(event: Event) -> list[str]:
    return list(event.get("categories") or [])


def _name_after_prefix(event: Event, prefix: str, category: str) -> str | None:
    summary = summary_of(event)
    if summary.startswith(prefix):
        return summary.removeprefix(prefix).strip()
    if category in categories_of(event):
        return summary.strip()
    return None


def course_name(event: Event) -> str | None:
    """Returns the course name of a lecture event, or ``None`` for non-lectures."""
    return _name_after_prefix(event, LECTURE_PREFIX, LECTURE_CATEGORY)


def deadline_name(event: Event) -> str | None:
    """Returns the name of a deadline event, or ``None`` for non-deadlines."""
    return _name_after_prefix(event, DEADLINE_PREFIX, DEADLINE_CATEGORY)


def is_exam(event: Event) -> bool:
    return summary_of(event).startswith(EXAM_PREFIX) or (
        EXAM_CATEGORY in categories_of(event)
    )


def clean_summary(summary: str) -> str:
    """Strips Polimi boilerplate from a title (e.g. ``"Lezione: Didattica - "``)."""
    if summary.startswith(LECTURE_PREFIX):
        return summary.removeprefix(LECTURE_PREFIX).strip()
    return summary


def start_date(event: Event) -> str:
    """Returns the ``YYYY-MM-DD`` start date of an event (or a placeholder)."""
    start = event.get("start") or {}
    date_str = start.get("dateTime", start.get("date", UNKNOWN_DATE))
    if "T" in date_str:
        date_str = date_str.split("T")[0]
    return str(date_str)


def parse_enrollment(description: str) -> Enrollment:
    if description.startswith("Iscritto"):
        return Enrollment.ENROLLED
    if description.startswith("Non iscritto"):
        return Enrollment.NOT_ENROLLED
    return Enrollment.UNKNOWN


@dataclass(frozen=True)
class ExamOccurrence:
    """A single exam session (appello): an exam title on a given date."""

    title: str
    date: str
    enrollment: Enrollment = Enrollment.UNKNOWN

    @property
    def key(self) -> str:
        """Stable identifier, also used as key in ``exam_states.json``."""
        return exam_key(self.title, self.date)


def exam_key(title: str, date: str) -> str:
    return f"{title} ({date})"


def title_from_exam_key(key: str) -> str:
    return key.rsplit(" (", 1)[0]


def exam_occurrence(event: Event) -> ExamOccurrence | None:
    """Returns the exam occurrence of an exam event, or ``None`` for non-exams."""
    if not is_exam(event):
        return None
    return ExamOccurrence(
        title=summary_of(event),
        date=start_date(event),
        enrollment=parse_enrollment(description_of(event)),
    )
