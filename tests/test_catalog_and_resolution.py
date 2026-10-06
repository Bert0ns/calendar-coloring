import pytest

from polimi_calendar_coloring.catalog import Catalog, discover
from polimi_calendar_coloring.events import Enrollment, ExamOccurrence
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import ExamPreference, Preferences
from polimi_calendar_coloring.resolution import (
    fill_missing_course_colors,
    fill_missing_exam_preferences,
    fill_missing_preferences,
)
from polimi_calendar_coloring.suggestions import suggest_color
from polimi_calendar_coloring.targets import SyncTarget

RED = ExamPreference(GoogleColor.TOMATO, True)
GREY = ExamPreference(GoogleColor.GRAPHITE, False)


def exam(title: str, date: str, description: str = "") -> dict:
    return {"summary": title, "description": description, "start": {"date": date}}


def lecture(course: str) -> dict:
    return {"summary": f"Lezione: Didattica - {course}"}


def test_discover_collects_courses_and_exams_in_order() -> None:
    catalog = discover(
        [
            lecture("B"),
            exam("Esame: X", "2027-01-01", "Iscritto"),
            lecture("A"),
            lecture("B"),
            {"summary": "Other"},
            exam("Esame: X", "2027-01-01", "Non iscritto"),
            exam("Esame: Y", "2027-02-01"),
        ]
    )
    assert catalog.courses == ("B", "A")
    assert len(catalog.exam_occurrences) == 3
    # Distinct exams: the first occurrence of a session wins.
    assert catalog.exams == (
        ExamOccurrence("Esame: X", "2027-01-01", Enrollment.ENROLLED),
        ExamOccurrence("Esame: Y", "2027-02-01", Enrollment.UNKNOWN),
    )


def test_discover_empty() -> None:
    assert discover([]) == Catalog()


def test_fill_missing_course_colors_keeps_existing() -> None:
    prefs = Preferences(course_colors={"A": GoogleColor.BANANA})
    fill_missing_course_colors(["A", "B"], prefs)
    assert prefs.course_colors == {
        "A": GoogleColor.BANANA,
        "B": suggest_color("B"),
    }


def occ(title: str, date: str, enrollment: Enrollment) -> ExamOccurrence:
    return ExamOccurrence(title, date, enrollment)


class TestFillMissingExamPreferences:
    def test_subscription_greys_out_later_sessions(self) -> None:
        prefs = Preferences()
        fill_missing_exam_preferences(
            [
                occ("Esame: X", "1", Enrollment.NOT_ENROLLED),
                occ("Esame: X", "2", Enrollment.ENROLLED),
                occ("Esame: X", "3", Enrollment.ENROLLED),
            ],
            prefs,
        )
        assert prefs.exams == {
            "Esame: X (1)": GREY,
            "Esame: X (2)": RED,
            "Esame: X (3)": GREY,
        }

    def test_saved_subscription_greys_out_new_sessions(self) -> None:
        prefs = Preferences(exams={"Esame: X (0)": RED})
        fill_missing_exam_preferences(
            [occ("Esame: X", "1", Enrollment.ENROLLED)], prefs
        )
        assert prefs.exams["Esame: X (1)"] == GREY

    def test_existing_entries_are_never_changed(self) -> None:
        custom = ExamPreference(GoogleColor.BASIL, False)
        prefs = Preferences(exams={"Esame: X (1)": custom})
        fill_missing_exam_preferences(
            [occ("Esame: X", "1", Enrollment.ENROLLED)], prefs
        )
        assert prefs.exams == {"Esame: X (1)": custom}

    def test_unknown_enrollment_is_left_unset(self) -> None:
        prefs = Preferences()
        fill_missing_exam_preferences([occ("Esame: X", "1", Enrollment.UNKNOWN)], prefs)
        assert prefs.exams == {}

    def test_does_not_mutate_input_preferences_titles(self) -> None:
        prefs = Preferences()
        fill_missing_exam_preferences(
            [occ("Esame: X", "1", Enrollment.ENROLLED)], prefs
        )
        assert prefs.subscribed_exam_titles() == {"Esame: X"}


@pytest.mark.parametrize(
    ("target", "has_courses", "has_exams"),
    [
        (SyncTarget.ALL, True, True),
        (SyncTarget.EXAMS, False, True),
        (SyncTarget.LECTURES, True, False),
        (SyncTarget.DEADLINES, False, False),
    ],
)
def test_fill_missing_preferences_respects_target(
    target: SyncTarget, has_courses: bool, has_exams: bool
) -> None:
    catalog = discover(
        [lecture("A"), exam("Esame: X", "1", "Iscritto"), {"summary": "Scadenza: D"}]
    )
    prefs = Preferences()
    fill_missing_preferences(catalog, prefs, target)
    assert bool(prefs.course_colors) is has_courses
    assert bool(prefs.exams) is has_exams
    assert bool(prefs.deadline_colors) is target.includes_deadlines


def test_discover_deadlines_and_category_events() -> None:
    catalog = discover(
        [
            {"summary": "Scadenza: Esame di laurea"},
            {"summary": "Tesi", "categories": ["Scadenza"]},
            {"summary": "Scadenza: Esame di laurea"},
            {"summary": "SENSOR SYSTEMS", "categories": ["Lezione"]},
            {"summary": "MOBILE", "categories": ["Esame"], "start": {"date": "d"}},
        ]
    )
    assert catalog.deadlines == ("Esame di laurea", "Tesi")
    assert catalog.courses == ("SENSOR SYSTEMS",)
    assert [e.title for e in catalog.exams] == ["MOBILE"]


def test_discover_classifies_each_event_once_exam_first() -> None:
    catalog = discover(
        [{"summary": "Lezione: Didattica - X", "categories": ["Esame", "Scadenza"]}]
    )
    assert [e.title for e in catalog.exams] == ["Lezione: Didattica - X"]
    assert catalog.courses == ()
    assert catalog.deadlines == ()


def test_fill_missing_deadline_colors() -> None:
    prefs = Preferences(deadline_colors={"A": GoogleColor.BANANA})
    fill_missing_preferences(Catalog(deadlines=("A", "B")), prefs, SyncTarget.DEADLINES)
    assert prefs.deadline_colors == {"A": GoogleColor.BANANA, "B": suggest_color("B")}
