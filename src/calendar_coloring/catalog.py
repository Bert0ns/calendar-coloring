"""Discovery of the courses and exams contained in the source calendar."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from calendar_coloring.events import (
    Event,
    ExamOccurrence,
    course_name,
    deadline_name,
    exam_occurrence,
)


@dataclass(frozen=True)
class Catalog:
    """Courses, exams and deadlines found in the source calendar, in event order."""

    courses: tuple[str, ...] = ()
    exam_occurrences: tuple[ExamOccurrence, ...] = ()
    """Every exam event, in order, duplicates included."""
    deadlines: tuple[str, ...] = ()

    @property
    def exams(self) -> tuple[ExamOccurrence, ...]:
        """Distinct exam sessions (first occurrence wins)."""
        seen: set[str] = set()
        unique: list[ExamOccurrence] = []
        for occurrence in self.exam_occurrences:
            if occurrence.key not in seen:
                seen.add(occurrence.key)
                unique.append(occurrence)
        return tuple(unique)


def discover(events: Iterable[Event]) -> Catalog:
    """Classifies events; the first match wins (exam, then lecture, then deadline),
    mirroring the order in which coloring strategies are applied."""
    courses: dict[str, None] = {}
    exams: list[ExamOccurrence] = []
    deadlines: dict[str, None] = {}
    for event in events:
        occurrence = exam_occurrence(event)
        if occurrence is not None:
            exams.append(occurrence)
            continue
        course = course_name(event)
        if course is not None:
            courses.setdefault(course, None)
            continue
        deadline = deadline_name(event)
        if deadline is not None:
            deadlines.setdefault(deadline, None)
    return Catalog(
        courses=tuple(courses),
        exam_occurrences=tuple(exams),
        deadlines=tuple(deadlines),
    )
