"""Choice of the source and target calendars. Pure Python: no Textual, no I/O."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from unical.profile import CalendarSettings
from unical.sync.models import CalendarInfo
from unical.sync.source import EventSource


class Role(Enum):
    SOURCE = "source"
    TARGET = "target"


@dataclass(frozen=True)
class CalendarSetup:
    """How the calendars of a TUI session are found and can be changed."""

    calendars: CalendarSettings
    """The calendars to start with (the profile's, with any override)."""
    source_for: Callable[[str], EventSource]
    """The events source for a source calendar name."""
    fixed_source: str | None = None
    """Label of the iCal feed used instead of a source calendar, if any. The
    source can't be changed from the TUI then."""
    overrides: Mapping[Role, str] = field(default_factory=dict)
    """Environment variables that override the calendars saved in the profile."""
    first_run: bool = False
    """True when there is no profile yet: the app opens the guided setup."""


@dataclass(frozen=True)
class CalendarChoice:
    name: str
    allowed: bool
    note: str = ""


def calendar_choices(
    calendars: Sequence[CalendarInfo],
    role: Role,
    current: CalendarSettings,
    fixed_source: bool = False,
) -> list[CalendarChoice]:
    """The calendars to offer for ``role``, telling which can't be chosen.

    Calendars are found by name, so a name shared by several calendars is
    flagged: the first one is used.
    """
    counts = Counter(calendar.name for calendar in calendars)
    choices: list[CalendarChoice] = []
    seen: set[str] = set()
    for calendar in calendars:
        if calendar.name in seen:
            continue
        seen.add(calendar.name)
        notes: list[str] = []
        allowed = True
        if role is Role.SOURCE:
            if calendar.name == current.source:
                notes.append("current")
            elif calendar.name == current.target:
                allowed = False
                notes.append("the target calendar")
        else:
            if calendar.name == current.target:
                notes.append("current")
            if calendar.name == current.source and not fixed_source:
                allowed = False
                notes.append("the source calendar")
            elif not calendar.writable:
                allowed = False
                notes.append("read-only")
        if calendar.primary:
            notes.append("primary")
        if counts[calendar.name] > 1:
            notes.append(f"{counts[calendar.name]} calendars have this name")
        choices.append(CalendarChoice(calendar.name, allowed, ", ".join(notes)))
    return choices


def target_name_error(
    name: str,
    calendars: Sequence[CalendarInfo],
    current: CalendarSettings,
    fixed_source: bool = False,
) -> str | None:
    """Why ``name`` can't be the target calendar, or ``None`` if it can.

    A name that is not in the calendar list is a new calendar, created when
    the changes are applied.
    """
    name = name.strip()
    if not name:
        return "Type a name for the calendar."
    if name == current.source and not fixed_source:
        return "The target can't be the source calendar."
    for calendar in calendars:
        if calendar.name == name and not calendar.writable:
            return f"'{name}' is read-only."
    return None
