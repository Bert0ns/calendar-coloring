from polimi_calendar_coloring.catalog import discover
from polimi_calendar_coloring.events import Enrollment, ExamOccurrence
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import ExamPreference, Preferences
from polimi_calendar_coloring.suggestions import suggest_color
from polimi_calendar_coloring.tui.model import ExamRow, ItemStatus, PreferencesDraft
from polimi_calendar_coloring.workflow import SyncOptions, SyncSession


def event(i: int, summary: str, day: str, description: str = "") -> dict:
    return {
        "id": f"evt{i:05d}",
        "summary": summary,
        "description": description,
        "start": {"date": day},
    }


EVENTS = [
    event(1, "Lezione: Didattica - CS", "2026-10-01"),
    event(2, "Lezione: Didattica - Math", "2026-10-02"),
    event(3, "Esame: CS", "2027-01-20", "Iscritto"),
    event(4, "Esame: CS", "2027-02-10"),
    event(5, "Esame: Math", "2027-01-25", "Non iscritto"),
    event(6, "Esame: Physics", "2027-02-01"),
    event(7, "Scadenza: Piano di studi", "2026-11-15"),
]


def make_draft(preferences: Preferences | None = None) -> PreferencesDraft:
    return PreferencesDraft(
        SyncSession(
            options=SyncOptions(),
            target_name="Tgt",
            source_events=EVENTS,
            catalog=discover(EVENTS),
            preferences=preferences or Preferences(),
        )
    )


def exam_rows_by_key(draft: PreferencesDraft) -> dict[str, ExamRow]:
    return {row.key: row for row in draft.exam_rows()}


def test_course_rows_tell_saved_suggested_and_modified_apart() -> None:
    draft = make_draft(Preferences(course_colors={"CS": GoogleColor.BASIL}))

    rows = {row.name: row for row in draft.course_rows()}
    assert rows["CS"].color is GoogleColor.BASIL
    assert rows["CS"].status is ItemStatus.SAVED
    assert rows["Math"].color is suggest_color("Math")
    assert rows["Math"].status is ItemStatus.SUGGESTED
    assert not draft.is_dirty

    draft.set_course_color("Math", GoogleColor.GRAPE)

    [_, math] = draft.course_rows()
    assert math.color is GoogleColor.GRAPE
    assert math.status is ItemStatus.MODIFIED
    assert draft.is_dirty

    draft.mark_saved()
    assert draft.course_rows()[1].status is ItemStatus.SAVED
    assert not draft.is_dirty


def test_deadline_rows() -> None:
    draft = make_draft()
    [row] = draft.deadline_rows()
    assert row.name == "Piano di studi"
    assert row.status is ItemStatus.SUGGESTED

    draft.set_deadline_color("Piano di studi", GoogleColor.BANANA)
    [row] = draft.deadline_rows()
    assert (row.color, row.status) == (GoogleColor.BANANA, ItemStatus.MODIFIED)


def test_exam_rows_show_what_the_automatic_rules_would_apply() -> None:
    rows = exam_rows_by_key(make_draft())

    first_cs = rows["Esame: CS (2027-01-20)"]
    assert (first_cs.subscribed, first_cs.color) == (True, GoogleColor.TOMATO)
    assert first_cs.status is ItemStatus.SUGGESTED
    assert first_cs.hint == "Iscritto"

    other_cs = rows["Esame: CS (2027-02-10)"]
    assert (other_cs.subscribed, other_cs.color) == (False, GoogleColor.GRAPHITE)
    assert other_cs.subscribed_to_other_date
    assert other_cs.hint == "another date subscribed"

    math = rows["Esame: Math (2027-01-25)"]
    assert (math.subscribed, math.hint) == (False, "Non iscritto")

    physics = rows["Esame: Physics (2027-02-01)"]
    assert (physics.subscribed, physics.color) == (None, None)
    assert physics.status is ItemStatus.UNSET
    assert physics.hint == ""


def test_exam_rows_do_not_change_the_preferences() -> None:
    draft = make_draft()
    draft.exam_rows()
    assert draft.preferences == Preferences()


def test_saved_exam_is_reported_as_saved() -> None:
    exam = ExamOccurrence("Esame: Physics", "2027-02-01", Enrollment.UNKNOWN)
    preferences = Preferences()
    preferences.set_exam(exam, ExamPreference(GoogleColor.SAGE, subscribed=True))

    row = make_draft(preferences).exam_row(exam.key)

    assert (row.subscribed, row.color) == (True, GoogleColor.SAGE)
    assert row.status is ItemStatus.SAVED


def test_toggle_subscription_moves_default_colors() -> None:
    draft = make_draft()
    key = "Esame: Physics (2027-02-01)"

    draft.toggle_exam_subscription(key)
    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (True, GoogleColor.TOMATO)
    assert row.status is ItemStatus.MODIFIED

    draft.toggle_exam_subscription(key)
    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (False, GoogleColor.GRAPHITE)


def test_toggle_subscription_keeps_custom_colors() -> None:
    draft = make_draft()
    key = "Esame: CS (2027-01-20)"
    draft.set_exam_color(key, GoogleColor.PEACOCK)

    draft.toggle_exam_subscription(key)

    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (False, GoogleColor.PEACOCK)


def test_set_exam_color_keeps_the_subscription() -> None:
    draft = make_draft()

    draft.set_exam_color("Esame: CS (2027-01-20)", GoogleColor.BASIL)
    draft.set_exam_color("Esame: Physics (2027-02-01)", GoogleColor.BASIL)

    rows = exam_rows_by_key(draft)
    assert rows["Esame: CS (2027-01-20)"].subscribed is True
    assert rows["Esame: Physics (2027-02-01)"].subscribed is False


def test_unsubscribing_frees_the_other_dates() -> None:
    draft = make_draft()
    draft.toggle_exam_subscription("Esame: CS (2027-01-20)")

    other = draft.exam_row("Esame: CS (2027-02-10)")
    assert other.status is ItemStatus.UNSET
    assert not other.subscribed_to_other_date
