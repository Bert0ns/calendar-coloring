"""Interactive terminal editor for preferences (the ``-i`` mode)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial
from typing import TypeVar

from polimi_calendar_coloring.catalog import Catalog
from polimi_calendar_coloring.cli.ansi import (
    BLUE,
    BOLD,
    CYAN,
    YELLOW,
    palette_lines,
    style,
)
from polimi_calendar_coloring.events import ExamOccurrence
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import ExamPreference, Preferences
from polimi_calendar_coloring.suggestions import (
    default_exam_color,
    is_subscribed_to_other_session,
    suggest_color,
    suggest_exam_subscription,
)
from polimi_calendar_coloring.targets import SyncTarget

T = TypeVar("T")

_YES = {"y", "yes"}
_NO = {"n", "no"}


def parse_yes_no(answer: str, default: bool | None) -> bool | None:
    """Parses a y/n answer. Empty input selects ``default``; invalid → ``None``."""
    answer = answer.strip().lower()
    if answer == "":
        return default
    if answer in _YES:
        return True
    if answer in _NO:
        return False
    return None


def parse_color(answer: str, default: GoogleColor) -> GoogleColor | None:
    """Parses a color ID (1-11). Empty input selects ``default``; invalid → ``None``."""
    answer = answer.strip()
    if answer == "":
        return default
    return GoogleColor.parse(answer)


def _yes_no_hint(default: bool | None) -> str:
    y = "Y" if default is True else "y"
    n = "N" if default is False else "n"
    return f"[{style(y, BOLD)}/{style(n, BOLD)}]"


class InteractivePreferenceEditor:
    """Asks the user, one item at a time, using injectable I/O functions."""

    def __init__(
        self,
        ask: Callable[[str], str] = input,
        say: Callable[[str], None] = print,
    ) -> None:
        self._ask = ask
        self._say = say

    def edit(
        self, catalog: Catalog, preferences: Preferences, target: SyncTarget
    ) -> None:
        if target.includes_exams:
            self.edit_exams(catalog.exams, preferences)
        if target.includes_lectures:
            self.edit_courses(catalog.courses, preferences)
        if target.includes_deadlines:
            self.edit_deadlines(catalog.deadlines, preferences)

    def edit_exams(
        self, exams: Iterable[ExamOccurrence], preferences: Preferences
    ) -> None:
        subscribed_titles = preferences.subscribed_exam_titles()
        # Answers given in this session, by title: other dates of the same exam
        # that have no saved state reuse them instead of asking again.
        decided: dict[str, ExamPreference] = {}
        for exam in exams:
            existing = preferences.exam(exam)
            if existing is None and exam.title in decided:
                reused = decided[exam.title]
                preferences.set_exam(exam, reused)
                self._say(
                    style(
                        f" ↳ Reusing '{exam.title}' decision for {exam.date} "
                        f"(color {reused.color.color_id}).",
                        BLUE,
                    )
                )
                continue
            self._say("\n" + style(f"📝 Exam: {exam.title} on {exam.date}", CYAN, BOLD))
            if is_subscribed_to_other_session(exam, existing, subscribed_titles):
                self._say(
                    style(
                        f" ↳ Note: Already subscribed to another date for "
                        f"'{exam.title}'.",
                        YELLOW,
                    )
                )

            default = suggest_exam_subscription(exam, existing, subscribed_titles)
            subscribed = self._ask_until_valid(
                f"❓ Subscribed to '{exam.title}' on {exam.date}? "
                f"{_yes_no_hint(default)}: ",
                partial(parse_yes_no, default=default),
                "Please answer 'y' or 'n' (or press Enter for default).",
            )

            default_color = (
                existing.color
                if existing is not None
                else default_exam_color(subscribed)
            )
            color = self._ask_color("this exam", default_color)

            preference = ExamPreference(color=color, subscribed=subscribed)
            preferences.set_exam(exam, preference)
            decided[exam.title] = preference
            if subscribed:
                subscribed_titles.add(exam.title)

    def edit_courses(self, courses: Iterable[str], preferences: Preferences) -> None:
        for course in courses:
            preferences.set_course_color(
                course,
                self._pick_named_color(
                    course,
                    preferences.course_color(course),
                    icon="🎨",
                    kind="Course",
                    subject=f"ALL lectures of '{course}'",
                ),
            )

    def edit_deadlines(
        self, deadlines: Iterable[str], preferences: Preferences
    ) -> None:
        for deadline in deadlines:
            preferences.set_deadline_color(
                deadline,
                self._pick_named_color(
                    deadline,
                    preferences.deadline_color(deadline),
                    icon="⏰",
                    kind="Deadline",
                    subject=f"'{deadline}'",
                ),
            )

    def _pick_named_color(
        self,
        name: str,
        saved: GoogleColor | None,
        icon: str,
        kind: str,
        subject: str,
    ) -> GoogleColor:
        status = f"Existing {kind}" if saved is not None else f"New {kind} Detected"
        self._say("\n" + style(f"{icon} {status}: ", CYAN) + style(f"'{name}'", BOLD))
        default = saved if saved is not None else suggest_color(name)
        return self._ask_color(subject, default)

    def _ask_color(self, subject: str, default: GoogleColor) -> GoogleColor:
        self._say("Available Google Calendar Colors:")
        for line in palette_lines():
            self._say(line)
        return self._ask_until_valid(
            f"❓ Pick a color ID for {subject} (1-11) "
            f"[Default {default.color_id}]: ",
            partial(parse_color, default=default),
            "Invalid choice. Please pick from 1 to 11.",
        )

    def _ask_until_valid(
        self,
        prompt: str,
        parse: Callable[[str], T | None],
        error: str,
    ) -> T:
        while True:
            value = parse(self._ask(style(prompt, CYAN)))
            if value is not None:
                return value
            self._say(style(f" ↳ {error}", YELLOW))
