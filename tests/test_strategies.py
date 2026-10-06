import pytest
from conftest import POLIMI

from calendar_coloring.palette import GoogleColor
from calendar_coloring.preferences import ExamPreference, Preferences
from calendar_coloring.strategies import (
    CompositeColoringStrategy,
    DeadlineColoringStrategy,
    EventColoringStrategy,
    ExamColoringStrategy,
    LectureColoringStrategy,
    strategy_for,
)
from calendar_coloring.suggestions import suggest_color
from calendar_coloring.targets import SyncTarget

LECTURE = {"summary": "Lezione: Didattica - Computer Security"}
EXAM = {"summary": "Esame: Security", "start": {"date": "2026-06-15"}}
OTHER = {"summary": "Meeting"}
DEADLINE = {"summary": "Scadenza: Esame di laurea"}

PREFS = Preferences(
    course_colors={"Computer Security": GoogleColor.BANANA},
    exams={"Security (2026-06-15)": ExamPreference(GoogleColor.GRAPE, True)},
    deadline_colors={"Esame di laurea": GoogleColor.SAGE},
)


class Fixed(EventColoringStrategy):
    def __init__(self, color: GoogleColor | None) -> None:
        self.color = color
        self.calls = 0

    def determine_color(self, event):
        self.calls += 1
        return self.color


def test_lecture_strategy_uses_saved_color() -> None:
    assert (
        LectureColoringStrategy(PREFS, POLIMI).determine_color(LECTURE)
        is GoogleColor.BANANA
    )


def test_lecture_strategy_falls_back_to_deterministic_color() -> None:
    strategy = LectureColoringStrategy(Preferences(), POLIMI)
    assert strategy.determine_color(LECTURE) is suggest_color("Computer Security")


def test_lecture_strategy_ignores_non_lectures() -> None:
    strategy = LectureColoringStrategy(PREFS, POLIMI)
    assert strategy.determine_color(EXAM) is None
    assert strategy.determine_color(OTHER) is None


def test_exam_strategy_uses_saved_color() -> None:
    assert (
        ExamColoringStrategy(PREFS, POLIMI).determine_color(EXAM) is GoogleColor.GRAPE
    )


def test_exam_strategy_returns_none_for_unknown_or_other_dates() -> None:
    strategy = ExamColoringStrategy(PREFS, POLIMI)
    assert strategy.determine_color({**EXAM, "start": {"date": "2026-07-01"}}) is None
    assert strategy.determine_color(LECTURE) is None
    assert strategy.determine_color(OTHER) is None


def test_strategies_are_pure_lookups() -> None:
    prefs = Preferences()
    LectureColoringStrategy(prefs, POLIMI).determine_color(LECTURE)
    ExamColoringStrategy(prefs, POLIMI).determine_color(EXAM)
    assert prefs == Preferences()


def test_composite_returns_first_match_and_short_circuits() -> None:
    first, second, third = (
        Fixed(None),
        Fixed(GoogleColor.SAGE),
        Fixed(GoogleColor.BASIL),
    )
    composite = CompositeColoringStrategy([first, second, third])
    assert composite.determine_color(OTHER) is GoogleColor.SAGE
    assert (first.calls, second.calls, third.calls) == (1, 1, 0)


def test_composite_without_match_or_strategies() -> None:
    assert CompositeColoringStrategy([Fixed(None)]).determine_color(OTHER) is None
    assert CompositeColoringStrategy([]).determine_color(OTHER) is None


def test_deadline_strategy() -> None:
    strategy = DeadlineColoringStrategy(PREFS, POLIMI)
    assert strategy.determine_color(DEADLINE) is GoogleColor.SAGE
    assert DeadlineColoringStrategy(Preferences(), POLIMI).determine_color(
        {"summary": "Tesi", "categories": ["Scadenza"]}
    ) is suggest_color("Tesi")
    assert strategy.determine_color(LECTURE) is None
    assert strategy.determine_color(EXAM) is None


@pytest.mark.parametrize(
    ("target", "lecture", "exam", "deadline"),
    [
        (SyncTarget.ALL, GoogleColor.BANANA, GoogleColor.GRAPE, GoogleColor.SAGE),
        (SyncTarget.EXAMS, None, GoogleColor.GRAPE, None),
        (SyncTarget.LECTURES, GoogleColor.BANANA, None, None),
        (SyncTarget.DEADLINES, None, None, GoogleColor.SAGE),
    ],
)
def test_strategy_for_target(target, lecture, exam, deadline) -> None:
    strategy = strategy_for(target, PREFS, POLIMI)
    assert strategy.determine_color(LECTURE) is lecture
    assert strategy.determine_color(EXAM) is exam
    assert strategy.determine_color(DEADLINE) is deadline
    assert strategy.determine_color(OTHER) is None
