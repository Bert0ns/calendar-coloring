import pytest
from conftest import POLIMI

from calendar_coloring.events import Enrollment, ExamOccurrence
from calendar_coloring.rules import (
    Classification,
    Classifier,
    Condition,
    EnrollmentRules,
    EventKind,
    Field,
    MatchKind,
    Rule,
)


class TestPolimi:
    """The built-in profile reproduces the PoliMi calendar format."""

    def test_course_name_strips_prefix_and_whitespace(self) -> None:
        assert POLIMI.course_name(
            {"summary": "Lezione: Didattica -   Data Bases 2  "}
        ) == ("Data Bases 2")
        assert POLIMI.course_name({"summary": "Esame: Data Bases 2"}) is None
        assert POLIMI.course_name({}) is None

    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            ("Lezione: Didattica - Computer Security", "Computer Security"),
            ("Lezione: Didattica -  ML ", "ML"),
            (
                "Esame: Computer Security - Appello 1",
                "Esame: Computer Security - Appello 1",
            ),
            ("Scadenza: Esame di laurea", "Scadenza: Esame di laurea"),
            ("Meeting", "Meeting"),
            ("", ""),
        ],
    )
    def test_target_title(self, raw: str, clean: str) -> None:
        assert POLIMI.target_title({"summary": raw}) == clean

    @pytest.mark.parametrize(
        ("description", "enrollment"),
        [
            ("Iscritto all'appello", Enrollment.ENROLLED),
            ("Iscritto", Enrollment.ENROLLED),
            ("Non iscritto all'appello", Enrollment.NOT_ENROLLED),
            ("non iscritto", Enrollment.UNKNOWN),
            ("Appello generico", Enrollment.UNKNOWN),
            ("", Enrollment.UNKNOWN),
            (None, Enrollment.UNKNOWN),
        ],
    )
    def test_enrollment(self, description: str | None, enrollment: Enrollment) -> None:
        assert POLIMI.enrollment({"description": description}) is enrollment

    def test_exam_occurrence_from_event(self) -> None:
        occ = POLIMI.exam_occurrence(
            {
                "summary": "Esame: Security",
                "description": "Iscritto",
                "start": {"dateTime": "2026-06-15T09:00:00+02:00"},
            }
        )
        assert occ == ExamOccurrence("Security", "2026-06-15", Enrollment.ENROLLED)
        assert occ.key == "Security (2026-06-15)"

    def test_exam_occurrence_ignores_non_exams(self) -> None:
        assert POLIMI.exam_occurrence({"summary": "Lezione: Didattica - X"}) is None

    def test_lecture_by_category_uses_whole_title(self) -> None:
        event = {"summary": " SENSOR SYSTEMS ", "categories": ["Lezione"]}
        assert POLIMI.course_name(event) == "SENSOR SYSTEMS"
        assert POLIMI.target_title(event) == " SENSOR SYSTEMS "

    def test_prefix_wins_over_category(self) -> None:
        event = {"summary": "Lezione: Didattica - CS", "categories": ["Lezione"]}
        assert POLIMI.course_name(event) == "CS"

    def test_exam_by_category(self) -> None:
        event = {
            "summary": "MOBILE APPLICATIONS",
            "description": "Non iscritto",
            "categories": ["Esame"],
            "start": {"date": "2026-09-08"},
        }
        assert POLIMI.exam_occurrence(event) == ExamOccurrence(
            "MOBILE APPLICATIONS", "2026-09-08", Enrollment.NOT_ENROLLED
        )

    def test_deadlines(self) -> None:
        assert POLIMI.deadline_name({"summary": "Scadenza: Esame di laurea"}) == (
            "Esame di laurea"
        )
        assert (
            POLIMI.deadline_name({"summary": "Tesi", "categories": ["Scadenza"]})
            == "Tesi"
        )
        assert POLIMI.deadline_name({"summary": "Esame: X"}) is None

    @pytest.mark.parametrize("categories", [None, [], ["Altro"]])
    def test_no_matching_category(self, categories: list[str] | None) -> None:
        event = {"summary": "Something", "categories": categories}
        assert POLIMI.classify(event) is None


def _title(match: MatchKind, value: str, **kwargs: bool) -> Condition:
    return Condition(Field.TITLE, match, value, **kwargs)


class TestCondition:
    @pytest.mark.parametrize(
        ("match", "value", "title", "matches"),
        [
            (MatchKind.STARTS_WITH, "Exam", "Exam - Algebra", True),
            (MatchKind.STARTS_WITH, "Exam", "Final Exam", False),
            (MatchKind.CONTAINS, "Exam", "Final Exam", True),
            (MatchKind.EQUALS, "Exam", "Exam", True),
            (MatchKind.EQUALS, "Exam", "Exam 2", False),
            (MatchKind.REGEX, r"^\[\w+\]", "[CS101] Algorithms", True),
            (MatchKind.REGEX, r"^\[\w+\]", "CS101 Algorithms", False),
            # Literal values are not regexes.
            (MatchKind.CONTAINS, "(", "Lab (A)", True),
        ],
    )
    def test_match_kinds(
        self, match: MatchKind, value: str, title: str, matches: bool
    ) -> None:
        assert bool(_title(match, value).search({"summary": title})) is matches

    def test_ignore_case(self) -> None:
        event = {"summary": "EXAM: Algebra"}
        assert _title(MatchKind.STARTS_WITH, "exam").search(event) is None
        assert _title(MatchKind.STARTS_WITH, "exam", ignore_case=True).search(event)

    @pytest.mark.parametrize(
        ("field", "event"),
        [
            (Field.DESCRIPTION, {"description": "Room 3 - Lab"}),
            (Field.LOCATION, {"location": "Lab building"}),
            (Field.CATEGORY, {"categories": ["Lecture", "Lab"]}),
        ],
    )
    def test_fields(self, field: Field, event: dict[str, object]) -> None:
        assert Condition(field, MatchKind.CONTAINS, "Lab").search(event)
        assert not Condition(field, MatchKind.CONTAINS, "Lab").search({})

    @pytest.mark.parametrize(
        ("match", "value"), [(MatchKind.REGEX, "(unclosed"), (MatchKind.EQUALS, "")]
    )
    def test_invalid_values(self, match: MatchKind, value: str) -> None:
        with pytest.raises(ValueError):
            _title(match, value)


class TestClassifier:
    def test_first_matching_rule_wins(self) -> None:
        classifier = Classifier(
            (
                Rule(EventKind.EXAM, _title(MatchKind.CONTAINS, "Exam")),
                Rule(EventKind.LECTURE, _title(MatchKind.CONTAINS, "Lecture")),
            )
        )
        assert classifier.classify({"summary": "Lecture before the Exam"}) == (
            Classification(
                EventKind.EXAM, "Lecture before the", "Lecture before the Exam"
            )
        )

    def test_regex_name_group(self) -> None:
        classifier = Classifier(
            (
                Rule(
                    EventKind.LECTURE,
                    _title(MatchKind.REGEX, r"^\[\w+\] (?P<name>.+?) - Lecture$"),
                    title="{name}",
                ),
            )
        )
        event = {"summary": "[CS101] Algorithms - Lecture"}
        assert classifier.course_name(event) == "Algorithms"
        assert classifier.target_title(event) == "Algorithms"

    def test_name_is_whole_title_for_other_fields(self) -> None:
        classifier = Classifier(
            (
                Rule(
                    EventKind.LECTURE,
                    Condition(Field.DESCRIPTION, MatchKind.STARTS_WITH, "Lecture"),
                ),
            )
        )
        event = {"summary": " Algebra ", "description": "Lecture, room 3"}
        assert classifier.course_name(event) == "Algebra"

    def test_name_falls_back_to_title_when_nothing_is_left(self) -> None:
        classifier = Classifier(
            (Rule(EventKind.DEADLINE, _title(MatchKind.EQUALS, "Thesis")),)
        )
        assert classifier.deadline_name({"summary": "Thesis"}) == "Thesis"

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("{title}", "Exam - Algebra"),
            ("{name}", "Algebra"),
            ("📝 {name}", "📝 Algebra"),
            ("{name} ({title})", "Algebra (Exam - Algebra)"),
            ("{other} {name}", "{other} Algebra"),
            ("   ", "Exam - Algebra"),
        ],
    )
    def test_title_templates(self, template: str, expected: str) -> None:
        classifier = Classifier(
            (
                Rule(
                    EventKind.EXAM,
                    _title(MatchKind.STARTS_WITH, "Exam - "),
                    title=template,
                ),
            )
        )
        assert classifier.target_title({"summary": "Exam - Algebra"}) == expected

    def test_no_rules_classifies_nothing(self) -> None:
        event = {"summary": "Esame: X", "description": "Iscritto"}
        assert Classifier().classify(event) is None
        assert Classifier().target_title(event) == "Esame: X"
        assert Classifier().enrollment(event) is Enrollment.UNKNOWN

    def test_not_enrolled_is_checked_first(self) -> None:
        classifier = Classifier(
            enrollment_rules=EnrollmentRules(
                enrolled=Condition(Field.DESCRIPTION, MatchKind.CONTAINS, "enrolled"),
                not_enrolled=Condition(
                    Field.DESCRIPTION, MatchKind.CONTAINS, "not enrolled"
                ),
            )
        )
        assert classifier.enrollment({"description": "You are enrolled"}) is (
            Enrollment.ENROLLED
        )
        assert classifier.enrollment({"description": "You are not enrolled"}) is (
            Enrollment.NOT_ENROLLED
        )
