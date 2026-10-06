"""Calendar events and the exam sessions found in them.

Which events are exams, lectures or deadlines is decided by the profile's
rules: see :mod:`calendar_coloring.rules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

Event = dict[str, Any]
"""A raw Google Calendar event resource."""

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


def start_date(event: Event) -> str:
    """Returns the ``YYYY-MM-DD`` start date of an event (or a placeholder)."""
    start = event.get("start") or {}
    date_str = start.get("dateTime", start.get("date", UNKNOWN_DATE))
    if "T" in date_str:
        date_str = date_str.split("T")[0]
    return str(date_str)


@dataclass(frozen=True)
class ExamOccurrence:
    """A single exam session (appello): an exam title on a given date."""

    title: str
    date: str
    enrollment: Enrollment = Enrollment.UNKNOWN

    @property
    def key(self) -> str:
        """Stable identifier, also used as key in the profile's ``exams``."""
        return exam_key(self.title, self.date)


def exam_key(title: str, date: str) -> str:
    return f"{title} ({date})"


def title_from_exam_key(key: str) -> str:
    return key.rsplit(" (", 1)[0]
