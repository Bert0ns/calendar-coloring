from conftest import POLIMI

from unical.catalog import discover
from unical.events import Enrollment, ExamOccurrence
from unical.palette import GoogleColor
from unical.preferences import ExamPreference, Preferences
from unical.profile import CalendarSettings, Profile
from unical.suggestions import suggest_color
from unical.tui.model import ExamRow, ItemStatus, PreferencesDraft
from unical.workflow import SyncOptions, SyncSession


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
            catalog=discover(EVENTS, POLIMI),
            profile=Profile(preferences=preferences or Preferences()),
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

    first_cs = rows["CS (2027-01-20)"]
    assert (first_cs.subscribed, first_cs.color) == (True, GoogleColor.TOMATO)
    assert first_cs.status is ItemStatus.SUGGESTED
    assert first_cs.hint == "enrolled"

    other_cs = rows["CS (2027-02-10)"]
    assert (other_cs.subscribed, other_cs.color) == (False, GoogleColor.GRAPHITE)
    assert other_cs.subscribed_to_other_date
    assert other_cs.hint == "another date subscribed"

    math = rows["Math (2027-01-25)"]
    assert (math.subscribed, math.hint) == (False, "not enrolled")

    physics = rows["Physics (2027-02-01)"]
    assert (physics.subscribed, physics.color) == (None, None)
    assert physics.status is ItemStatus.UNSET
    assert physics.hint == ""


def test_exam_rows_do_not_change_the_preferences() -> None:
    draft = make_draft()
    draft.exam_rows()
    assert draft.preferences == Preferences()


def test_saved_exam_is_reported_as_saved() -> None:
    exam = ExamOccurrence("Physics", "2027-02-01", Enrollment.UNKNOWN)
    preferences = Preferences()
    preferences.set_exam(exam, ExamPreference(GoogleColor.SAGE, subscribed=True))

    row = make_draft(preferences).exam_row(exam.key)

    assert (row.subscribed, row.color) == (True, GoogleColor.SAGE)
    assert row.status is ItemStatus.SAVED


def test_toggle_subscription_moves_default_colors() -> None:
    draft = make_draft()
    key = "Physics (2027-02-01)"

    draft.toggle_exam_subscription(key)
    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (True, GoogleColor.TOMATO)
    assert row.status is ItemStatus.MODIFIED

    draft.toggle_exam_subscription(key)
    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (False, GoogleColor.GRAPHITE)


def test_toggle_subscription_keeps_custom_colors() -> None:
    draft = make_draft()
    key = "CS (2027-01-20)"
    draft.set_exam_color(key, GoogleColor.PEACOCK)

    draft.toggle_exam_subscription(key)

    row = draft.exam_row(key)
    assert (row.subscribed, row.color) == (False, GoogleColor.PEACOCK)


def test_set_exam_color_keeps_the_subscription() -> None:
    draft = make_draft()

    draft.set_exam_color("CS (2027-01-20)", GoogleColor.BASIL)
    draft.set_exam_color("Physics (2027-02-01)", GoogleColor.BASIL)

    rows = exam_rows_by_key(draft)
    assert rows["CS (2027-01-20)"].subscribed is True
    assert rows["Physics (2027-02-01)"].subscribed is False


def test_unsubscribing_frees_the_other_dates() -> None:
    draft = make_draft()
    draft.toggle_exam_subscription("CS (2027-01-20)")

    other = draft.exam_row("CS (2027-02-10)")
    assert other.status is ItemStatus.UNSET
    assert not other.subscribed_to_other_date


def test_calendar_changes_are_saved_without_the_unsaved_edits() -> None:
    draft = make_draft()
    draft.set_course_color("CS", GoogleColor.BASIL)

    to_save = draft.set_calendars(CalendarSettings(source="Uni", target="Mine"))

    assert to_save.calendars == CalendarSettings(source="Uni", target="Mine")
    assert to_save.preferences == Preferences()
    assert draft.session.profile.calendars == to_save.calendars
    assert draft.is_dirty  # the course color is still to save
    draft.mark_saved()
    assert draft.saved_profile == draft.session.profile


def test_draft_can_start_from_an_older_saved_profile() -> None:
    saved = Profile(preferences=Preferences(course_colors={"CS": GoogleColor.SAGE}))
    draft = make_draft()

    resumed = PreferencesDraft(draft.session, saved)

    assert resumed.is_dirty
    assert resumed.course_rows()[0].status is ItemStatus.MODIFIED
