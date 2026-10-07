from __future__ import annotations

from enum import Enum

from unical.rules import EventKind


class SyncTarget(Enum):
    """Which kinds of events get colored."""

    ALL = "all"
    EXAMS = "exams"
    LECTURES = "lectures"
    DEADLINES = "deadlines"

    def covers(self, kind: EventKind | None) -> bool:
        """True if events of this kind are synced; ``None`` is an unclassified
        event, only synced when the target is everything."""
        if self is SyncTarget.ALL:
            return True
        return (
            (kind is EventKind.EXAM and self.includes_exams)
            or (kind is EventKind.LECTURE and self.includes_lectures)
            or (kind is EventKind.DEADLINE and self.includes_deadlines)
        )

    @property
    def includes_exams(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.EXAMS)

    @property
    def includes_lectures(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.LECTURES)

    @property
    def includes_deadlines(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.DEADLINES)
