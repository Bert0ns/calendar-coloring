"""Academic semester inference and date window calculations."""

from __future__ import annotations

import calendar
from datetime import date


def is_leap_year(year: int) -> bool:
    return calendar.isleap(year)


def feb_last_day(year: int) -> int:
    return 29 if is_leap_year(year) else 28


def current_semester_window(
    today: date | None = None,
) -> tuple[date, date, str]:
    """Infers the active semester window for a given date (defaults to today).

    - Semester 2 (Spring / Summer): March 1 -> September 15
    - Semester 1 (Fall / Winter): September 16 -> February 28/29

    Returns:
        tuple of (window_from, window_to, semester_label)
    """
    if today is None:
        today = date.today()

    year = today.year
    month = today.month
    day = today.day

    # Check if in Semester 2 (March 1 to September 15 inclusive)
    if (month == 3 and day >= 1) or (3 < month < 9) or (month == 9 and day <= 15):
        return (date(year, 3, 1), date(year, 9, 15), "Semester 2")

    # Check if in Semester 1 (Fall start: September 16 to December 31)
    if (month == 9 and day >= 16) or (month > 9):
        end_year = year + 1
        return (
            date(year, 9, 16),
            date(end_year, 2, feb_last_day(end_year)),
            "Semester 1",
        )

    # In Semester 1 (Winter conclusion: January 1 to February 28/29)
    return (
        date(year - 1, 9, 16),
        date(year, 2, feb_last_day(year)),
        "Semester 1",
    )
