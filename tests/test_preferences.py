from unical.palette import GoogleColor
from unical.preferences import ExamPreference, Preferences


def test_subscribed_exam_titles() -> None:
    prefs = Preferences(
        exams={
            "A (2027-01-01)": ExamPreference(GoogleColor.TOMATO, True),
            "A (2027-02-01)": ExamPreference(GoogleColor.GRAPHITE, False),
            "B (2027-01-01)": ExamPreference(GoogleColor.GRAPHITE, False),
            "C (Prova) (2027-01-01)": ExamPreference(GoogleColor.TOMATO, True),
        }
    )
    assert prefs.subscribed_exam_titles() == {"A", "C (Prova)"}
