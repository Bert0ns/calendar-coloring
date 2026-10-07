"""Discovery of the courses and exams contained in the source calendar."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from unical.events import Event, ExamOccurrence, start_date
from unical.rules import Classifier, EventKind


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


def discover(events: Iterable[Event], classifier: Classifier) -> Catalog:
    """Lists what the events contain, as classified by the profile's rules."""
    courses: dict[str, None] = {}
    exams: list[ExamOccurrence] = []
    deadlines: dict[str, None] = {}
    for event in events:
        classification = classifier.classify(event)
        if classification is None:
            continue
        if classification.kind is EventKind.EXAM:
            exams.append(
                ExamOccurrence(
                    title=classification.name,
                    date=start_date(event),
                    enrollment=classifier.enrollment(event),
                )
            )
        elif classification.kind is EventKind.LECTURE:
            courses.setdefault(classification.name, None)
        else:
            deadlines.setdefault(classification.name, None)
    return Catalog(
        courses=tuple(courses),
        exam_occurrences=tuple(exams),
        deadlines=tuple(deadlines),
    )
