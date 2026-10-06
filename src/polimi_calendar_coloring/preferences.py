"""User preferences (course/deadline colors, exam states) and their persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polimi_calendar_coloring.events import ExamOccurrence, title_from_exam_key
from polimi_calendar_coloring.palette import GoogleColor


@dataclass(frozen=True)
class ExamPreference:
    color: GoogleColor
    subscribed: bool


@dataclass
class Preferences:
    """In-memory user preferences. Insertion order is preserved on save."""

    course_colors: dict[str, GoogleColor] = field(default_factory=dict)
    exams: dict[str, ExamPreference] = field(default_factory=dict)
    deadline_colors: dict[str, GoogleColor] = field(default_factory=dict)

    def course_color(self, course: str) -> GoogleColor | None:
        return self.course_colors.get(course)

    def set_course_color(self, course: str, color: GoogleColor) -> None:
        self.course_colors[course] = color

    def deadline_color(self, deadline: str) -> GoogleColor | None:
        return self.deadline_colors.get(deadline)

    def set_deadline_color(self, deadline: str, color: GoogleColor) -> None:
        self.deadline_colors[deadline] = color

    def exam(self, occurrence: ExamOccurrence) -> ExamPreference | None:
        return self.exams.get(occurrence.key)

    def set_exam(self, occurrence: ExamOccurrence, preference: ExamPreference) -> None:
        self.exams[occurrence.key] = preference

    def subscribed_exam_titles(self) -> set[str]:
        """Titles of exams the user is subscribed to on at least one date."""
        return {
            title_from_exam_key(key)
            for key, preference in self.exams.items()
            if preference.subscribed
        }


WarningSink = Callable[[str], None]
JsonObject = dict[str, Any]


def _ignore_warning(_: str) -> None:
    pass


class JsonPreferencesRepository:
    """Loads/saves :class:`Preferences` from/to JSON files.

    The on-disk format is backwards compatible with previous versions:

    - ``course_colors.json`` / ``deadline_colors.json``: ``{"<name>": "<color id>"}``
    - ``exam_states.json``: ``{"<title> (<date>)": {"color": "<id>", "subscribed": bool}}``

    A file is only rewritten when its content changed since it was loaded, so a
    corrupted file that is not touched by the current run is left untouched.
    Writes are atomic: a crash never leaves a half-written file behind.
    """

    def __init__(
        self,
        course_colors_path: Path,
        exam_states_path: Path,
        deadline_colors_path: Path,
        on_warning: WarningSink = _ignore_warning,
    ) -> None:
        self.course_colors_path = Path(course_colors_path)
        self.exam_states_path = Path(exam_states_path)
        self.deadline_colors_path = Path(deadline_colors_path)
        self._on_warning = on_warning
        self._snapshots: dict[Path, JsonObject] = {}

    def load(self) -> Preferences:
        preferences = Preferences(
            course_colors=self._parse_colors(
                self._read(self.course_colors_path), "course"
            ),
            exams=self._parse_exams(self._read(self.exam_states_path)),
            deadline_colors=self._parse_colors(
                self._read(self.deadline_colors_path), "deadline"
            ),
        )
        self._snapshots = self._serialize(preferences)
        return preferences

    def save(self, preferences: Preferences) -> None:
        for path, data in self._serialize(preferences).items():
            if data != self._snapshots.get(path, {}):
                self._write(path, data)
                self._snapshots[path] = data

    # -- parsing -------------------------------------------------------------

    def _read(self, path: Path) -> JsonObject:
        if not path.exists():
            return {}
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._on_warning(f"'{path}' is corrupted. Starting fresh.")
            return {}
        if not isinstance(data, dict):
            self._on_warning(f"'{path}' does not contain a JSON object. Ignoring it.")
            return {}
        return data

    def _parse_colors(self, raw: JsonObject, kind: str) -> dict[str, GoogleColor]:
        colors: dict[str, GoogleColor] = {}
        for name, color_id in raw.items():
            color = GoogleColor.parse(color_id)
            if color is None:
                self._on_warning(
                    f"Ignoring invalid color {color_id!r} for {kind} '{name}'."
                )
                continue
            colors[name] = color
        return colors

    def _parse_exams(self, raw: JsonObject) -> dict[str, ExamPreference]:
        exams: dict[str, ExamPreference] = {}
        for key, entry in raw.items():
            if not isinstance(entry, dict):
                entry = {}
            color = GoogleColor.parse(entry.get("color"))
            if color is None:
                self._on_warning(f"Ignoring invalid saved state for exam '{key}'.")
                continue
            exams[key] = ExamPreference(
                color=color, subscribed=bool(entry.get("subscribed", False))
            )
        return exams

    # -- serialization -------------------------------------------------------

    def _serialize(self, preferences: Preferences) -> dict[Path, JsonObject]:
        return {
            self.course_colors_path: {
                name: color.color_id
                for name, color in preferences.course_colors.items()
            },
            self.exam_states_path: {
                key: {"color": pref.color.color_id, "subscribed": pref.subscribed}
                for key, pref in preferences.exams.items()
            },
            self.deadline_colors_path: {
                name: color.color_id
                for name, color in preferences.deadline_colors.items()
            },
        }

    @staticmethod
    def _write(path: Path, data: JsonObject) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
