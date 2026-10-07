import pytest

from unical.profile import CalendarSettings
from unical.sync.models import CalendarInfo
from unical.tui.setup import (
    CalendarChoice,
    Role,
    calendar_choices,
    target_name_error,
)

CALENDARS = [
    CalendarInfo("me", "me@example.com", writable=True, primary=True),
    CalendarInfo("uni", "Polimi", writable=False),
    CalendarInfo("col", "Polimi Colored", writable=True),
    CalendarInfo("hol", "Holidays", writable=False),
    CalendarInfo("dup1", "Work", writable=True),
    CalendarInfo("dup2", "Work", writable=True),
]
CURRENT = CalendarSettings(source="Polimi", target="Polimi Colored")


def test_source_choices() -> None:
    assert calendar_choices(CALENDARS, Role.SOURCE, CURRENT) == [
        CalendarChoice("me@example.com", True, "primary"),
        CalendarChoice("Polimi", True, "current"),
        CalendarChoice("Polimi Colored", False, "the target calendar"),
        CalendarChoice("Holidays", True),
        CalendarChoice("Work", True, "2 calendars have this name"),
    ]


def test_target_choices() -> None:
    assert calendar_choices(CALENDARS, Role.TARGET, CURRENT) == [
        CalendarChoice("me@example.com", True, "primary"),
        CalendarChoice("Polimi", False, "the source calendar"),
        CalendarChoice("Polimi Colored", True, "current"),
        CalendarChoice("Holidays", False, "read-only"),
        CalendarChoice("Work", True, "2 calendars have this name"),
    ]


def test_with_an_ical_feed_the_source_calendar_can_be_the_target() -> None:
    current = CalendarSettings(source="me@example.com", target="Polimi Colored")
    choices = calendar_choices(CALENDARS, Role.TARGET, current, fixed_source=True)
    assert choices[0] == CalendarChoice("me@example.com", True, "primary")


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("New calendar", None),
        ("  Polimi Colored  ", None),
        ("Work", None),
        ("", "Type a name for the calendar."),
        ("   ", "Type a name for the calendar."),
        ("Polimi", "The target can't be the source calendar."),
        ("Holidays", "'Holidays' is read-only."),
    ],
)
def test_target_name_error(name: str, error: str | None) -> None:
    assert target_name_error(name, CALENDARS, CURRENT) == error


def test_target_name_can_be_the_source_calendar_with_an_ical_feed() -> None:
    current = CalendarSettings(source="Mine", target="Polimi Colored")
    assert target_name_error("Mine", [], current, fixed_source=True) is None
