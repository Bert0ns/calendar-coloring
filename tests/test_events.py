import pytest

from unical.events import exam_key, start_date, title_from_exam_key


@pytest.mark.parametrize(
    ("event", "date"),
    [
        ({"start": {"dateTime": "2027-01-20T09:00:00+01:00"}}, "2027-01-20"),
        ({"start": {"date": "2027-01-25"}}, "2027-01-25"),
        ({"start": {}}, "Unknown Date"),
        ({}, "Unknown Date"),
        ({"start": None}, "Unknown Date"),
    ],
)
def test_start_date(event: dict, date: str) -> None:
    assert start_date(event) == date


@pytest.mark.parametrize(
    "title",
    [
        "Esame: Security",
        "Esame: Software Engineering (Prova finale)",
        "Esame: a (b) (c)",
    ],
)
def test_exam_key_round_trip_with_parentheses_in_title(title: str) -> None:
    assert title_from_exam_key(exam_key(title, "2027-02-20")) == title
