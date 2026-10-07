"""The profile: everything the tool knows about one student's calendar.

A profile holds the calendars to sync, the rules that classify events, and the
colors and exam subscriptions the student chose. It is stored in one JSON file::

    {
        "version": 1,
        "name": "Politecnico di Milano",
        "calendars": {"source": "Calendar", "target": "Calendar Colored",
                      "time_zone": null},
        "rules": [
            {"kind": "exam", "field": "title", "match": "starts_with",
             "value": "Esame: ", "ignore_case": false, "title": "{title}"}
        ],
        "enrollment": {
            "enrolled": {"field": "description", "match": "starts_with",
                         "value": "Iscritto", "ignore_case": false},
            "not_enrolled": null
        },
        "courses": {"<course>": "<color id>"},
        "exams": {"<exam> (<YYYY-MM-DD>)": {"color": "<color id>", "subscribed": true}},
        "deadlines": {"<deadline>": "<color id>"}
    }

The iCal URL is deliberately not part of the profile: it is a secret, and the
profile is meant to be committed (e.g. by the GitHub Actions sync).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from unical.palette import GoogleColor
from unical.preferences import ExamPreference, Preferences
from unical.presets import POLIMI_ENROLLMENT, POLIMI_NAME, POLIMI_RULES
from unical.rules import (
    TITLE_PLACEHOLDER,
    Classifier,
    Condition,
    EnrollmentRules,
    EventKind,
    Field,
    MatchKind,
    Rule,
)

FORMAT_VERSION = 1
DEFAULT_SOURCE_CALENDAR = "Calendar"
DEFAULT_TARGET_CALENDAR = "Calendar Colored"

WarningSink = Callable[[str], None]
JsonObject = dict[str, Any]


@dataclass
class CalendarSettings:
    source: str = DEFAULT_SOURCE_CALENDAR
    """Name of the Google calendar to read (unless an iCal URL is given)."""
    target: str = DEFAULT_TARGET_CALENDAR
    """Name of the Google calendar to write, created if missing."""
    time_zone: str | None = None
    """IANA time zone of the target calendar when it's created (e.g.
    ``"Europe/Rome"``). ``None``: the one of the user's primary calendar."""


@dataclass
class Profile:
    name: str = ""
    calendars: CalendarSettings = field(default_factory=CalendarSettings)
    rules: list[Rule] = field(default_factory=list)
    enrollment: EnrollmentRules = field(default_factory=EnrollmentRules)
    preferences: Preferences = field(default_factory=Preferences)

    @property
    def classifier(self) -> Classifier:
        return Classifier(tuple(self.rules), self.enrollment)


def time_zone_error(name: str) -> str | None:
    """Why ``name`` is not a time zone, or ``None`` if it is one."""
    if not name.strip():
        return "Type a time zone, e.g. Europe/Rome."
    try:
        ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return f"Unknown time zone '{name.strip()}': use a name like Europe/Rome."
    return None


def polimi_profile() -> Profile:
    """The profile used when there is none yet."""
    return Profile(
        name=POLIMI_NAME, rules=list(POLIMI_RULES), enrollment=POLIMI_ENROLLMENT
    )


def _ignore_warning(_: str) -> None:
    pass


class JsonProfileRepository:
    """Loads/saves a :class:`Profile` from/to a JSON file.

    A missing file gives the built-in PoliMi profile, and so do missing
    ``rules``/``enrollment`` keys for those parts. Invalid entries are
    skipped with a warning. The file is only rewritten when its content
    changed since it was loaded, and writes are atomic: a crash never leaves a
    half-written file behind.
    """

    def __init__(self, path: Path, on_warning: WarningSink = _ignore_warning) -> None:
        self.path = Path(path)
        self._on_warning = on_warning
        self._snapshot: JsonObject | None = None

    def load(self) -> Profile:
        raw = self._read()
        profile = polimi_profile() if raw is None else self._parse(raw)
        self._snapshot = serialize(profile) if raw is not None else None
        return profile

    def save(self, profile: Profile) -> None:
        data = serialize(profile)
        if data != self._snapshot:
            self._write(data)
            self._snapshot = copy.deepcopy(data)

    # -- parsing -------------------------------------------------------------

    def _read(self) -> JsonObject | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._on_warning(f"'{self.path}' is corrupted. Starting fresh.")
            return None
        if not isinstance(data, dict):
            self._on_warning(
                f"'{self.path}' does not contain a JSON object. Starting fresh."
            )
            return None
        return data

    def _parse(self, raw: JsonObject) -> Profile:
        version = raw.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            self._on_warning(
                f"'{self.path}' has format version {version!r}, expected "
                f"{FORMAT_VERSION}. Reading it anyway."
            )
        return Profile(
            name=str(raw.get("name") or ""),
            calendars=self._parse_calendars(self._section(raw, "calendars")),
            # Missing rules mean the built-in ones; an empty list means none.
            rules=(
                self._parse_rules(raw["rules"])
                if "rules" in raw
                else list(POLIMI_RULES)
            ),
            enrollment=(
                self._parse_enrollment(self._section(raw, "enrollment"))
                if "enrollment" in raw
                else POLIMI_ENROLLMENT
            ),
            preferences=Preferences(
                course_colors=self._parse_colors(
                    self._section(raw, "courses"), "course"
                ),
                exams=self._parse_exams(self._section(raw, "exams")),
                deadline_colors=self._parse_colors(
                    self._section(raw, "deadlines"), "deadline"
                ),
            ),
        )

    def _section(self, raw: JsonObject, key: str) -> JsonObject:
        try:
            return _object(raw.get(key))
        except ValueError:
            self._on_warning(f"Ignoring '{key}': it must be a JSON object.")
            return {}

    def _parse_calendars(self, raw: JsonObject) -> CalendarSettings:
        defaults = CalendarSettings()
        time_zone = raw.get("time_zone") or None
        if time_zone is not None and time_zone_error(str(time_zone)) is not None:
            self._on_warning(f"Ignoring unknown time zone {time_zone!r}.")
            time_zone = None
        return CalendarSettings(
            source=str(raw.get("source") or defaults.source),
            target=str(raw.get("target") or defaults.target),
            time_zone=None if time_zone is None else str(time_zone),
        )

    def _parse_rules(self, raw: object) -> list[Rule]:
        if not isinstance(raw, list):
            self._on_warning("Ignoring the rules: they must be a list.")
            return []
        rules: list[Rule] = []
        for index, entry in enumerate(raw, start=1):
            try:
                entry = _object(entry)
                rules.append(
                    Rule(
                        kind=EventKind(entry.get("kind")),
                        condition=_parse_condition(entry),
                        title=str(entry.get("title") or TITLE_PLACEHOLDER),
                    )
                )
            except ValueError as exc:
                self._on_warning(f"Ignoring rule {index}: {exc}.")
        return rules

    def _parse_enrollment(self, raw: JsonObject) -> EnrollmentRules:
        conditions: dict[str, Condition | None] = {}
        for key in ("enrolled", "not_enrolled"):
            entry = raw.get(key)
            conditions[key] = None
            if entry is None:
                continue
            try:
                conditions[key] = _parse_condition(_object(entry))
            except ValueError as exc:
                self._on_warning(f"Ignoring the '{key}' enrollment rule: {exc}.")
        return EnrollmentRules(**conditions)

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
            entry = entry if isinstance(entry, dict) else {}
            color = GoogleColor.parse(entry.get("color"))
            if color is None:
                self._on_warning(f"Ignoring invalid saved state for exam '{key}'.")
                continue
            exams[key] = ExamPreference(
                color=color, subscribed=bool(entry.get("subscribed", False))
            )
        return exams

    # -- writing -------------------------------------------------------------

    def _write(self, data: JsonObject) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise


def _object(raw: object) -> JsonObject:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {raw!r}")
    return raw


def _parse_condition(raw: JsonObject) -> Condition:
    return Condition(
        field=Field(raw.get("field")),
        match=MatchKind(raw.get("match")),
        value=str(raw.get("value") or ""),
        ignore_case=bool(raw.get("ignore_case", False)),
    )


def _condition_json(condition: Condition | None) -> JsonObject | None:
    if condition is None:
        return None
    return {
        "field": condition.field.value,
        "match": condition.match.value,
        "value": condition.value,
        "ignore_case": condition.ignore_case,
    }


def serialize(profile: Profile) -> JsonObject:
    prefs = profile.preferences
    return {
        "version": FORMAT_VERSION,
        "name": profile.name,
        "calendars": {
            "source": profile.calendars.source,
            "target": profile.calendars.target,
            "time_zone": profile.calendars.time_zone,
        },
        "rules": [
            {
                "kind": rule.kind.value,
                **(_condition_json(rule.condition) or {}),
                "title": rule.title,
            }
            for rule in profile.rules
        ],
        "enrollment": {
            "enrolled": _condition_json(profile.enrollment.enrolled),
            "not_enrolled": _condition_json(profile.enrollment.not_enrolled),
        },
        "courses": {
            name: color.color_id for name, color in prefs.course_colors.items()
        },
        "exams": {
            key: {"color": pref.color.color_id, "subscribed": pref.subscribed}
            for key, pref in prefs.exams.items()
        },
        "deadlines": {
            name: color.color_id for name, color in prefs.deadline_colors.items()
        },
    }
