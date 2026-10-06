from __future__ import annotations

from enum import Enum


class SyncTarget(Enum):
    """Which kinds of events get colored."""

    ALL = "all"
    EXAMS = "exams"
    LECTURES = "lectures"
    DEADLINES = "deadlines"

    @property
    def includes_exams(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.EXAMS)

    @property
    def includes_lectures(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.LECTURES)

    @property
    def includes_deadlines(self) -> bool:
        return self in (SyncTarget.ALL, SyncTarget.DEADLINES)
