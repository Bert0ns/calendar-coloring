"""Coloring strategies: pure lookups from an event to a color.

Strategies never prompt nor perform I/O; they only read :class:`Preferences`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from polimi_calendar_coloring.events import (
    Event,
    course_name,
    deadline_name,
    exam_occurrence,
)
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import Preferences
from polimi_calendar_coloring.suggestions import suggest_color
from polimi_calendar_coloring.targets import SyncTarget


class EventColoringStrategy(ABC):
    @abstractmethod
    def determine_color(self, event: Event) -> GoogleColor | None:
        """Returns the color for the event, or ``None`` if it does not apply."""


class CompositeColoringStrategy(EventColoringStrategy):
    """Returns the color of the first strategy that matches."""

    def __init__(self, strategies: Sequence[EventColoringStrategy]) -> None:
        self.strategies = list(strategies)

    def determine_color(self, event: Event) -> GoogleColor | None:
        for strategy in self.strategies:
            color = strategy.determine_color(event)
            if color is not None:
                return color
        return None


class LectureColoringStrategy(EventColoringStrategy):
    """Colors lectures with their course color (or its deterministic suggestion)."""

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def determine_color(self, event: Event) -> GoogleColor | None:
        course = course_name(event)
        if course is None:
            return None
        return self.preferences.course_color(course) or suggest_color(course)


class ExamColoringStrategy(EventColoringStrategy):
    """Colors exam sessions with their saved color (``None`` if unknown)."""

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def determine_color(self, event: Event) -> GoogleColor | None:
        occurrence = exam_occurrence(event)
        if occurrence is None:
            return None
        preference = self.preferences.exam(occurrence)
        return preference.color if preference is not None else None


class DeadlineColoringStrategy(EventColoringStrategy):
    """Colors deadlines with their saved color (or its deterministic suggestion)."""

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def determine_color(self, event: Event) -> GoogleColor | None:
        deadline = deadline_name(event)
        if deadline is None:
            return None
        return self.preferences.deadline_color(deadline) or suggest_color(deadline)


def strategy_for(target: SyncTarget, preferences: Preferences) -> EventColoringStrategy:
    strategies: list[EventColoringStrategy] = []
    if target.includes_exams:
        strategies.append(ExamColoringStrategy(preferences))
    if target.includes_lectures:
        strategies.append(LectureColoringStrategy(preferences))
    if target.includes_deadlines:
        strategies.append(DeadlineColoringStrategy(preferences))
    return CompositeColoringStrategy(strategies)
