import pytest

from calendar_coloring.events import (
    Enrollment,
    ExamOccurrence,
    clean_summary,
    course_name,
    deadline_name,
    exam_key,
    exam_occurrence,
    parse_enrollment,
    start_date,
    title_from_exam_key,
)


def test_course_name_strips_prefix_and_whitespace() -> None:
    assert course_name({"summary": "Lezione: Didattica -   Data Bases 2  "}) == (
        "Data Bases 2"
    )
    assert course_name({"summary": "Esame: Data Bases 2"}) is None
    assert course_name({}) is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Lezione: Didattica - Computer Security", "Computer Security"),
        ("Lezione: Didattica -  ML ", "ML"),
        (
            "Esame: Computer Security - Appello 1",
            "Esame: Computer Security - Appello 1",
        ),
        ("Meeting", "Meeting"),
        ("", ""),
    ],
)
def test_clean_summary(raw: str, clean: str) -> None:
    assert clean_summary(raw) == clean


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
    ("description", "enrollment"),
    [
        ("Iscritto all'appello", Enrollment.ENROLLED),
        ("Iscritto", Enrollment.ENROLLED),
        ("Non iscritto all'appello", Enrollment.NOT_ENROLLED),
        ("non iscritto", Enrollment.UNKNOWN),
        ("Appello generico", Enrollment.UNKNOWN),
        ("", Enrollment.UNKNOWN),
    ],
)
def test_parse_enrollment(description: str, enrollment: Enrollment) -> None:
    assert parse_enrollment(description) is enrollment


def test_exam_occurrence_from_event() -> None:
    occ = exam_occurrence(
        {
            "summary": "Esame: Security",
            "description": "Iscritto",
            "start": {"dateTime": "2026-06-15T09:00:00+02:00"},
        }
    )
    assert occ == ExamOccurrence("Esame: Security", "2026-06-15", Enrollment.ENROLLED)
    assert occ.key == "Esame: Security (2026-06-15)"


def test_exam_occurrence_tolerates_null_description() -> None:
    occ = exam_occurrence({"summary": "Esame: X", "description": None})
    assert occ is not None
    assert occ.enrollment is Enrollment.UNKNOWN


def test_exam_occurrence_ignores_non_exams() -> None:
    assert exam_occurrence({"summary": "Lezione: Didattica - X"}) is None


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


class TestCategories:
    def test_lecture_by_category_uses_whole_title(self) -> None:
        event = {"summary": " SENSOR SYSTEMS ", "categories": ["Lezione"]}
        assert course_name(event) == "SENSOR SYSTEMS"

    def test_prefix_wins_over_category(self) -> None:
        event = {"summary": "Lezione: Didattica - CS", "categories": ["Lezione"]}
        assert course_name(event) == "CS"

    def test_exam_by_category(self) -> None:
        event = {
            "summary": "MOBILE APPLICATIONS",
            "description": "Non iscritto",
            "categories": ["Esame"],
            "start": {"date": "2026-09-08"},
        }
        assert exam_occurrence(event) == ExamOccurrence(
            "MOBILE APPLICATIONS", "2026-09-08", Enrollment.NOT_ENROLLED
        )

    def test_deadlines(self) -> None:
        assert deadline_name({"summary": "Scadenza: Esame di laurea"}) == (
            "Esame di laurea"
        )
        assert deadline_name({"summary": "Tesi", "categories": ["Scadenza"]}) == "Tesi"
        assert deadline_name({"summary": "Esame: X"}) is None

    @pytest.mark.parametrize("categories", [None, [], ["Altro"]])
    def test_no_matching_category(self, categories) -> None:
        event = {"summary": "Something", "categories": categories}
        assert course_name(event) is None
        assert deadline_name(event) is None
        assert exam_occurrence(event) is None

    def test_deadline_titles_are_not_cleaned(self) -> None:
        assert clean_summary("Scadenza: Esame di laurea") == "Scadenza: Esame di laurea"
