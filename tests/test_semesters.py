from datetime import date

import pytest

from unical.semesters import current_semester_window, feb_last_day, is_leap_year


def test_leap_year() -> None:
    assert is_leap_year(2024) is True
    assert is_leap_year(2025) is False
    assert is_leap_year(2026) is False
    assert is_leap_year(2000) is True
    assert is_leap_year(1900) is False
    assert feb_last_day(2024) == 29
    assert feb_last_day(2025) == 28


@pytest.mark.parametrize(
    ("test_date", "expected_from", "expected_to", "expected_label"),
    [
        # Semester 2 boundaries
        (date(2026, 3, 1), date(2026, 3, 1), date(2026, 9, 15), "Semester 2"),
        (date(2026, 6, 15), date(2026, 3, 1), date(2026, 9, 15), "Semester 2"),
        (date(2026, 9, 15), date(2026, 3, 1), date(2026, 9, 15), "Semester 2"),
        # Semester 1 (Fall part)
        (date(2026, 9, 16), date(2026, 9, 16), date(2027, 2, 28), "Semester 1"),
        (date(2026, 12, 31), date(2026, 9, 16), date(2027, 2, 28), "Semester 1"),
        # Semester 1 (Winter part, non-leap year 2027)
        (date(2027, 1, 1), date(2026, 9, 16), date(2027, 2, 28), "Semester 1"),
        (date(2027, 2, 28), date(2026, 9, 16), date(2027, 2, 28), "Semester 1"),
        # Semester 1 ending in leap year (2023 Fall -> 2024 Winter)
        (date(2023, 10, 1), date(2023, 9, 16), date(2024, 2, 29), "Semester 1"),
        (date(2024, 1, 15), date(2023, 9, 16), date(2024, 2, 29), "Semester 1"),
        (date(2024, 2, 29), date(2023, 9, 16), date(2024, 2, 29), "Semester 1"),
        # Semester 2 in leap year (2024)
        (date(2024, 3, 1), date(2024, 3, 1), date(2024, 9, 15), "Semester 2"),
    ],
)
def test_current_semester_window(
    test_date: date,
    expected_from: date,
    expected_to: date,
    expected_label: str,
) -> None:
    w_from, w_to, label = current_semester_window(test_date)
    assert w_from == expected_from
    assert w_to == expected_to
    assert label == expected_label
