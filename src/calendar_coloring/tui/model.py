"""View model of the preferences editor. Pure Python: no Textual, no I/O.

It edits the :class:`Preferences` of a :class:`SyncSession` in place and tells,
for each course/exam/deadline, what will be applied and where it comes from.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum

from calendar_coloring.events import Enrollment, ExamOccurrence
from calendar_coloring.palette import GoogleColor
from calendar_coloring.preferences import ExamPreference, Preferences
from calendar_coloring.profile import CalendarSettings, Profile
from calendar_coloring.resolution import fill_missing_exam_preferences
from calendar_coloring.suggestions import (
    default_exam_color,
    is_subscribed_to_other_session,
    suggest_color,
)
from calendar_coloring.workflow import SyncSession


class ItemStatus(Enum):
    SAVED = "saved"
    MODIFIED = "modified"
    """Changed in this session, not saved yet."""
    SUGGESTED = "suggested"
    """Never chosen: the automatic rules decide (and save it on the next sync)."""
    UNSET = "unset"
    """Never chosen and no automatic rule applies: the event keeps its color."""


@dataclass(frozen=True)
class ColorRow:
    """A course or a deadline."""

    name: str
    color: GoogleColor
    status: ItemStatus


@dataclass(frozen=True)
class ExamRow:
    exam: ExamOccurrence
    subscribed: bool | None
    color: GoogleColor | None
    status: ItemStatus
    subscribed_to_other_date: bool

    @property
    def key(self) -> str:
        return self.exam.key

    @property
    def hint(self) -> str:
        """What the source calendar says about the enrollment."""
        hints = {
            Enrollment.ENROLLED: "enrolled",
            Enrollment.NOT_ENROLLED: "not enrolled",
            Enrollment.UNKNOWN: "",
        }
        parts = [hints[self.exam.enrollment]]
        if self.subscribed_to_other_date:
            parts.append("another date subscribed")
        return " · ".join(part for part in parts if part)


def _status(current: object, saved: object) -> ItemStatus:
    if current != saved:
        return ItemStatus.MODIFIED
    return ItemStatus.SUGGESTED if current is None else ItemStatus.SAVED


class PreferencesDraft:
    """Edits the profile of a session, remembering what was saved.

    :param saved: the profile as saved, when the session's profile already
        has unsaved changes (e.g. the events were reloaded from another source).
    """

    def __init__(self, session: SyncSession, saved: Profile | None = None) -> None:
        self.session = session
        self._saved = copy.deepcopy(saved if saved is not None else session.profile)

    @property
    def preferences(self) -> Preferences:
        return self.session.preferences

    @property
    def saved_profile(self) -> Profile:
        return copy.deepcopy(self._saved)

    @property
    def is_dirty(self) -> bool:
        """True if there are changes not saved yet."""
        return self.session.profile != self._saved

    def mark_saved(self) -> None:
        self._saved = copy.deepcopy(self.session.profile)

    def set_calendars(self, calendars: CalendarSettings) -> Profile:
        """Changes the calendars, which are saved right away: returns the profile
        to save, with the new calendars but without the unsaved edits."""
        self.session.profile.calendars = copy.deepcopy(calendars)
        self._saved.calendars = copy.deepcopy(calendars)
        return self.saved_profile

    # -- rows ----------------------------------------------------------------

    def course_rows(self) -> list[ColorRow]:
        return [
            self._color_row(
                name,
                self.preferences.course_colors,
                self._saved.preferences.course_colors,
            )
            for name in self.session.catalog.courses
        ]

    def deadline_rows(self) -> list[ColorRow]:
        return [
            self._color_row(
                name,
                self.preferences.deadline_colors,
                self._saved.preferences.deadline_colors,
            )
            for name in self.session.catalog.deadlines
        ]

    def exam_rows(self) -> list[ExamRow]:
        # What a sync would apply: the user's choices, completed by the rules.
        effective = copy.deepcopy(self.preferences)
        fill_missing_exam_preferences(self.session.catalog.exam_occurrences, effective)
        subscribed_titles = effective.subscribed_exam_titles()
        return [
            self._exam_row(exam, effective, subscribed_titles)
            for exam in self.session.catalog.exams
        ]

    def exam_row(self, key: str) -> ExamRow:
        return next(row for row in self.exam_rows() if row.key == key)

    # -- edits ---------------------------------------------------------------

    def set_course_color(self, name: str, color: GoogleColor) -> None:
        self.preferences.set_course_color(name, color)

    def set_deadline_color(self, name: str, color: GoogleColor) -> None:
        self.preferences.set_deadline_color(name, color)

    def set_exam_color(self, key: str, color: GoogleColor) -> None:
        row = self.exam_row(key)
        self.preferences.set_exam(
            row.exam, ExamPreference(color=color, subscribed=bool(row.subscribed))
        )

    def toggle_exam_subscription(self, key: str) -> None:
        """Flips the subscription. A default color follows the new status, a
        custom color is kept."""
        row = self.exam_row(key)
        was_subscribed = bool(row.subscribed)
        color = row.color
        if color is None or color is default_exam_color(was_subscribed):
            color = default_exam_color(not was_subscribed)
        self.preferences.set_exam(
            row.exam, ExamPreference(color=color, subscribed=not was_subscribed)
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _color_row(
        name: str,
        current: dict[str, GoogleColor],
        saved: dict[str, GoogleColor],
    ) -> ColorRow:
        color = current.get(name)
        return ColorRow(
            name=name,
            color=color or suggest_color(name),
            status=_status(color, saved.get(name)),
        )

    def _exam_row(
        self,
        exam: ExamOccurrence,
        effective: Preferences,
        subscribed_titles: set[str],
    ) -> ExamRow:
        chosen = self.preferences.exam(exam)
        applied = effective.exam(exam)
        if chosen is not None:
            status = _status(chosen, self._saved.preferences.exam(exam))
        elif applied is not None:
            status = ItemStatus.SUGGESTED
        else:
            status = ItemStatus.UNSET
        return ExamRow(
            exam=exam,
            subscribed=applied.subscribed if applied is not None else None,
            color=applied.color if applied is not None else None,
            status=status,
            subscribed_to_other_date=is_subscribed_to_other_session(
                exam, applied, subscribed_titles
            ),
        )
