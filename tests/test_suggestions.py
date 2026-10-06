import pytest

from calendar_coloring.events import Enrollment, ExamOccurrence
from calendar_coloring.palette import GoogleColor
from calendar_coloring.preferences import ExamPreference
from calendar_coloring.suggestions import (
    auto_exam_preference,
    default_exam_color,
    is_subscribed_to_other_session,
    suggest_color,
    suggest_exam_subscription,
)

RED = ExamPreference(GoogleColor.TOMATO, True)
GREY = ExamPreference(GoogleColor.GRAPHITE, False)


@pytest.mark.parametrize(
    ("course", "color_id"),
    # Values produced by the legacy implementation: must never change, or every
    # user's lecture colors would be reshuffled.
    [
        ("Computer Security", "3"),
        ("Machine Learning", "7"),
        ("Data Bases 2", "7"),
        ("Algoritmi e Principi dell'Informatica", "10"),
    ],
)
def test_course_color_is_stable_across_versions(course: str, color_id: str) -> None:
    assert suggest_color(course).color_id == color_id


def test_course_color_ignores_case_and_surrounding_whitespace() -> None:
    assert suggest_color("  computer security ") is suggest_color("Computer Security")


def test_default_exam_color() -> None:
    assert default_exam_color(True) is GoogleColor.TOMATO
    assert default_exam_color(False) is GoogleColor.GRAPHITE


def occ(enrollment: Enrollment, title: str = "Esame: X") -> ExamOccurrence:
    return ExamOccurrence(title, "2027-01-01", enrollment)


class TestAutoExamPreference:
    def test_enrolled_is_red_and_subscribed(self) -> None:
        assert auto_exam_preference(occ(Enrollment.ENROLLED), set()) == RED

    def test_not_enrolled_is_grey(self) -> None:
        assert auto_exam_preference(occ(Enrollment.NOT_ENROLLED), set()) == GREY

    def test_unknown_has_no_preference(self) -> None:
        assert auto_exam_preference(occ(Enrollment.UNKNOWN), set()) is None

    @pytest.mark.parametrize("enrollment", list(Enrollment))
    def test_already_subscribed_elsewhere_wins_over_description(
        self, enrollment: Enrollment
    ) -> None:
        assert auto_exam_preference(occ(enrollment), {"Esame: X"}) == GREY

    def test_other_titles_do_not_interfere(self) -> None:
        assert auto_exam_preference(occ(Enrollment.ENROLLED), {"Esame: Y"}) == RED


class TestSuggestExamSubscription:
    def test_existing_preference_wins(self) -> None:
        assert suggest_exam_subscription(occ(Enrollment.NOT_ENROLLED), RED, set())
        assert not suggest_exam_subscription(occ(Enrollment.ENROLLED), GREY, set())

    def test_enrolled_suggests_yes_even_if_subscribed_elsewhere(self) -> None:
        assert suggest_exam_subscription(occ(Enrollment.ENROLLED), None, {"Esame: X"})

    def test_not_enrolled_suggests_no(self) -> None:
        assert (
            suggest_exam_subscription(occ(Enrollment.NOT_ENROLLED), None, set())
            is False
        )

    def test_subscribed_elsewhere_suggests_no(self) -> None:
        assert (
            suggest_exam_subscription(occ(Enrollment.UNKNOWN), None, {"Esame: X"})
            is False
        )

    def test_no_hint_means_no_default(self) -> None:
        assert suggest_exam_subscription(occ(Enrollment.UNKNOWN), None, set()) is None


@pytest.mark.parametrize(
    ("titles", "existing", "expected"),
    [
        (set(), None, False),
        ({"Esame: X"}, None, True),
        ({"Esame: X"}, GREY, True),
        ({"Esame: X"}, RED, False),
    ],
)
def test_is_subscribed_to_other_session(
    titles: set[str], existing: ExamPreference | None, expected: bool
) -> None:
    assert (
        is_subscribed_to_other_session(occ(Enrollment.UNKNOWN), existing, titles)
        is expected
    )
