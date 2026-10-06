import re

import pytest

from polimi_calendar_coloring.catalog import discover
from polimi_calendar_coloring.cli.prompts import (
    InteractivePreferenceEditor,
    parse_color,
    parse_yes_no,
)
from polimi_calendar_coloring.events import Enrollment, ExamOccurrence
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import ExamPreference, Preferences
from polimi_calendar_coloring.suggestions import suggest_color
from polimi_calendar_coloring.targets import SyncTarget

ANSI = re.compile(r"\033\[[0-9;]*m")


class ScriptedIO:
    """Feeds scripted answers and records everything shown to the user."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.output: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(ANSI.sub("", prompt))
        if not self.answers:
            raise AssertionError(f"Unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.output.append(ANSI.sub("", text))

    def editor(self) -> InteractivePreferenceEditor:
        return InteractivePreferenceEditor(ask=self.ask, say=self.say)

    @property
    def text(self) -> str:
        return "\n".join(self.output)


@pytest.mark.parametrize(
    ("answer", "default", "expected"),
    [
        ("y", None, True),
        (" YES ", False, True),
        ("n", True, False),
        ("No", None, False),
        ("", True, True),
        ("", False, False),
        ("", None, None),
        ("maybe", True, None),
    ],
)
def test_parse_yes_no(answer: str, default: bool | None, expected: bool | None) -> None:
    assert parse_yes_no(answer, default) is expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", GoogleColor.SAGE),
        ("11", GoogleColor.TOMATO),
        (" 3 ", GoogleColor.GRAPE),
        ("12", None),
        ("0", None),
        ("red", None),
    ],
)
def test_parse_color(answer: str, expected: GoogleColor | None) -> None:
    assert parse_color(answer, GoogleColor.SAGE) is expected


EXAM = ExamOccurrence("Esame: Security", "2026-06-15", Enrollment.ENROLLED)


class TestEditExams:
    def test_enter_keeps_saved_values(self) -> None:
        prefs = Preferences(exams={EXAM.key: ExamPreference(GoogleColor.GRAPE, True)})
        io = ScriptedIO("", "")
        io.editor().edit_exams([EXAM], prefs)
        assert prefs.exam(EXAM) == ExamPreference(GoogleColor.GRAPE, True)
        assert "[Y/n]" in io.prompts[0]
        assert "[Default 3]" in io.prompts[1]

    def test_defaults_follow_description_for_new_exams(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("", "")
        io.editor().edit_exams([EXAM], prefs)
        assert prefs.exam(EXAM) == ExamPreference(GoogleColor.TOMATO, True)

    def test_answering_no_proposes_grey(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("n", "")
        io.editor().edit_exams([EXAM], prefs)
        assert prefs.exam(EXAM) == ExamPreference(GoogleColor.GRAPHITE, False)
        assert "[Default 8]" in io.prompts[1]

    def test_custom_color(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("y", "7")
        io.editor().edit_exams([EXAM], prefs)
        assert prefs.exam(EXAM) == ExamPreference(GoogleColor.PEACOCK, True)

    def test_reprompts_until_valid(self) -> None:
        unknown = ExamOccurrence("Esame: X", "2027-01-01", Enrollment.UNKNOWN)
        prefs = Preferences()
        io = ScriptedIO("", "perhaps", "y", "42", "abc", "2")
        io.editor().edit_exams([unknown], prefs)
        assert prefs.exam(unknown) == ExamPreference(GoogleColor.SAGE, True)
        assert "[y/n]" in io.prompts[0]
        assert io.text.count("Please answer 'y' or 'n'") == 2
        assert io.text.count("Invalid choice") == 2

    def test_other_dates_without_saved_state_reuse_the_answer(self) -> None:
        first = ExamOccurrence("Esame: X", "2027-01-01", Enrollment.ENROLLED)
        second = ExamOccurrence("Esame: X", "2027-02-01", Enrollment.NOT_ENROLLED)
        prefs = Preferences()
        io = ScriptedIO("y", "5")

        io.editor().edit_exams([first, second], prefs)

        assert len(io.prompts) == 2  # asked once per exam title
        assert prefs.exam(second) == prefs.exam(first)
        assert prefs.exam(second) == ExamPreference(GoogleColor.BANANA, True)
        assert "Reusing 'Esame: X' decision for 2027-02-01 (color 5)." in io.text

    def test_saved_dates_are_still_asked_with_a_warning(self) -> None:
        first = ExamOccurrence("Esame: X", "2027-01-01", Enrollment.UNKNOWN)
        second = ExamOccurrence("Esame: X", "2027-02-01", Enrollment.UNKNOWN)
        saved = ExamPreference(GoogleColor.GRAPHITE, False)
        prefs = Preferences(exams={second.key: saved})
        io = ScriptedIO("y", "", "", "")

        io.editor().edit_exams([first, second], prefs)

        assert len(io.prompts) == 4
        assert "[y/N]" in io.prompts[2]
        assert "Already subscribed to another date for 'Esame: X'" in io.text
        assert prefs.exam(second) == saved

    def test_reuse_is_per_title(self) -> None:
        a = ExamOccurrence("Esame: A", "2027-01-01", Enrollment.ENROLLED)
        b = ExamOccurrence("Esame: B", "2027-01-01", Enrollment.ENROLLED)
        io = ScriptedIO("", "", "", "")
        io.editor().edit_exams([a, b], Preferences())
        assert len(io.prompts) == 4

    def test_no_warning_for_the_subscribed_session_itself(self) -> None:
        prefs = Preferences(exams={EXAM.key: ExamPreference(GoogleColor.TOMATO, True)})
        io = ScriptedIO("", "")
        io.editor().edit_exams([EXAM], prefs)
        assert "Already subscribed" not in io.text

    def test_shows_palette(self) -> None:
        io = ScriptedIO("", "")
        io.editor().edit_exams([EXAM], Preferences())
        for color in GoogleColor:
            assert f"{color.color_id}: {color.label}" in io.text


class TestEditCourses:
    def test_enter_keeps_saved_color(self) -> None:
        prefs = Preferences(course_colors={"CS": GoogleColor.FLAMINGO})
        io = ScriptedIO("")
        io.editor().edit_courses(["CS"], prefs)
        assert prefs.course_color("CS") is GoogleColor.FLAMINGO
        assert "Existing Course: 'CS'" in io.text
        assert "[Default 4]" in io.prompts[0]

    def test_new_course_defaults_to_deterministic_color(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("")
        io.editor().edit_courses(["Machine Learning"], prefs)
        assert prefs.course_color("Machine Learning") is suggest_color(
            "Machine Learning"
        )
        assert "New Course Detected" in io.text

    def test_custom_color_after_invalid_input(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("99", "10")
        io.editor().edit_courses(["CS"], prefs)
        assert prefs.course_color("CS") is GoogleColor.BASIL


class TestEditDeadlines:
    def test_enter_keeps_saved_color(self) -> None:
        prefs = Preferences(deadline_colors={"Esame di laurea": GoogleColor.BANANA})
        io = ScriptedIO("")
        io.editor().edit_deadlines(["Esame di laurea"], prefs)
        assert prefs.deadline_color("Esame di laurea") is GoogleColor.BANANA
        assert "⏰ Existing Deadline: 'Esame di laurea'" in io.text
        assert (
            "Pick a color ID for 'Esame di laurea' (1-11) [Default 5]" in io.prompts[0]
        )

    def test_new_deadline_defaults_to_deterministic_color(self) -> None:
        prefs = Preferences()
        io = ScriptedIO("")
        io.editor().edit_deadlines(["Consegna tesi"], prefs)
        assert prefs.deadline_color("Consegna tesi") is suggest_color("Consegna tesi")
        assert "New Deadline Detected" in io.text

    def test_custom_color(self) -> None:
        prefs = Preferences()
        ScriptedIO("abc", "9").editor().edit_deadlines(["X"], prefs)
        assert prefs.deadline_color("X") is GoogleColor.BLUEBERRY


@pytest.mark.parametrize(
    ("target", "expected_prompts"),
    [
        (SyncTarget.ALL, 4),
        (SyncTarget.EXAMS, 2),
        (SyncTarget.LECTURES, 1),
        (SyncTarget.DEADLINES, 1),
    ],
)
def test_edit_respects_target(target: SyncTarget, expected_prompts: int) -> None:
    catalog = discover(
        [
            {"summary": "Lezione: Didattica - CS"},
            {"summary": "Lezione: Didattica - CS"},
            {"summary": "Esame: CS", "description": "Iscritto", "start": {"date": "d"}},
            {"summary": "Esame: CS", "description": "Iscritto", "start": {"date": "d"}},
            {"summary": "Scadenza: Tesi"},
        ]
    )
    io = ScriptedIO(*[""] * expected_prompts)
    prefs = Preferences()
    io.editor().edit(catalog, prefs, target)
    assert len(io.prompts) == expected_prompts
    assert bool(prefs.course_colors) is target.includes_lectures
    assert bool(prefs.exams) is target.includes_exams
    assert bool(prefs.deadline_colors) is target.includes_deadlines
