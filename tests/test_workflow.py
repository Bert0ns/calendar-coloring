from datetime import date
from unittest.mock import MagicMock

import pytest
from conftest import FakeCalendarGateway, profile_repository, save_profile, saved

from unical.catalog import Catalog
from unical.config import Config
from unical.palette import GoogleColor
from unical.preferences import Preferences
from unical.profile import CalendarSettings, Profile
from unical.reporting import NullReporter
from unical.sync import (
    CalendarInfo,
    GoogleCalendarSource,
    SourceCalendarNotFoundError,
    SyncService,
)
from unical.targets import SyncTarget
from unical.workflow import SyncOptions, SyncWorkflow

SOURCE = [
    {
        "id": "lec00001",
        "summary": "Lezione: Didattica - CS",
        "start": {"date": "2026-09-20"},
        "end": {"date": "2026-09-21"},
    },
    {
        "id": "eam00001",
        "summary": "Esame: CS",
        "description": "Iscritto",
        "start": {"date": "2027-01-20"},
        "end": {"date": "2027-01-21"},
    },
]


class RecordingEditor:
    def __init__(self) -> None:
        self.calls: list[tuple[Catalog, SyncTarget]] = []

    def edit(self, catalog: Catalog, preferences: Preferences, target: SyncTarget):
        self.calls.append((catalog, target))
        preferences.set_course_color("CS", GoogleColor.BASIL)


class Runner:
    """Runs the workflow reading the source from the fake gateway by name."""

    def __init__(self, config: Config, gateway: FakeCalendarGateway, editor, reporter):
        repo = profile_repository(config)
        self.gateway = gateway
        self.workflow = SyncWorkflow(
            SyncService(gateway), repo, reporter or NullReporter(), editor
        )

    def run(self, options: SyncOptions, source: str, target: str):
        return self.workflow.run(
            options, GoogleCalendarSource(self.gateway, source), target
        )


def make_workflow(
    config: Config, gateway: FakeCalendarGateway, editor=None, reporter=None
) -> Runner:
    return Runner(config, gateway, editor, reporter)


def test_full_sync_creates_target_and_saves_preferences(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    reporter = MagicMock(wraps=NullReporter())

    outcome = make_workflow(config, gateway, reporter=reporter).run(
        SyncOptions(), "Src", "Tgt"
    )

    assert outcome.succeeded
    assert outcome.result is not None
    assert outcome.result.created_target_calendar
    assert outcome.result.inserted == 2
    assert {e["summary"]: e["colorId"] for e in gateway.events_of("Tgt")} == {
        "CS": "3",
        "Esame: CS": "11",
    }
    assert saved(config, "courses") == {"CS": "3"}
    assert saved(config, "exams") == {
        "CS (2027-01-20)": {"color": "11", "subscribed": True}
    }
    assert [name for name, _, _ in reporter.method_calls] == [
        "sync_started",
        "target_calendar_missing",
        "plan_ready",
        "applying",
        "sync_finished",
    ]


def test_dry_run_writes_nothing(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    editor = RecordingEditor()

    outcome = make_workflow(config, gateway, editor).run(
        SyncOptions(interactive=True, dry_run=True), "Src", "Tgt"
    )

    assert outcome.result is None
    assert outcome.succeeded
    assert len(outcome.plan.mutations) == 2
    # Preferences chosen interactively are used for the preview...
    insert = next(m for m in outcome.plan.mutations if m.summary == "CS")
    assert insert.body["colorId"] == GoogleColor.BASIL.color_id
    # ...but nothing is persisted anywhere.
    assert gateway.batches == []
    assert gateway.events_of("Tgt") == []
    assert saved(config, "courses") == {}
    assert saved(config, "exams") == {}


def test_dry_run_does_not_create_missing_target(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    outcome = make_workflow(config, gateway).run(
        SyncOptions(dry_run=True), "Src", "Tgt"
    )
    assert gateway.created == []
    assert outcome.plan.target_calendar_id is None


def test_interactive_uses_editor_instead_of_auto_fill(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    editor = RecordingEditor()

    make_workflow(config, gateway, editor).run(
        SyncOptions(target=SyncTarget.LECTURES, interactive=True), "Src", "Tgt"
    )

    [(catalog, target)] = editor.calls
    assert catalog.courses == ("CS",)
    assert target is SyncTarget.LECTURES
    assert saved(config, "courses") == {"CS": "10"}
    assert saved(config, "exams") == {}


def test_interactive_without_editor_is_a_programming_error(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    with pytest.raises(ValueError):
        make_workflow(config, gateway).run(SyncOptions(interactive=True), "Src", "Tgt")


def test_missing_source_raises_before_touching_anything(config: Config) -> None:
    gateway = FakeCalendarGateway()
    with pytest.raises(SourceCalendarNotFoundError):
        make_workflow(config, gateway).run(SyncOptions(), "Src", "Tgt")
    assert gateway.created == []
    assert saved(config, "courses") == {}


def test_failures_make_outcome_unsuccessful(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    gateway.fail_event_ids = {"lec00001"}
    outcome = make_workflow(config, gateway).run(SyncOptions(), "Src", "Tgt")
    assert not outcome.succeeded


def test_pruned_and_startless_events_are_not_discovered(config: Config) -> None:
    from datetime import date

    events = [
        {
            "id": "lecold01",
            "summary": "Lezione: Didattica - Past Course",
            "start": {"date": "2026-01-10"},
        },
        {"id": "nostart01", "summary": "Lezione: Didattica - No Start"},
        *SOURCE,
    ]
    gateway = FakeCalendarGateway({"Src": events, "Tgt": []})
    editor = RecordingEditor()

    make_workflow(config, gateway, editor).run(
        SyncOptions(interactive=True, prune_before=date(2026, 9, 1)), "Src", "Tgt"
    )

    [(catalog, _)] = editor.calls
    assert catalog.courses == ("CS",)
    assert [e.title for e in catalog.exams] == ["CS"]


def test_deadlines_are_colored_and_saved(config: Config) -> None:
    deadline = {
        "id": "dead0001",
        "summary": "Scadenza: Esame di laurea",
        "start": {"date": "2026-10-20"},
        "end": {"date": "2026-10-21"},
    }
    gateway = FakeCalendarGateway({"Src": [deadline], "Tgt": []})

    make_workflow(config, gateway).run(
        SyncOptions(target=SyncTarget.DEADLINES), "Src", "Tgt"
    )

    [event] = gateway.events_of("Tgt")
    assert event["summary"] == "Scadenza: Esame di laurea"  # prefix kept
    assert saved(config, "deadlines") == {"Esame di laurea": event["colorId"]}
    assert saved(config, "courses") == {}


# -- phase by phase (as driven by the TUI) -----------------------------------


def test_preview_completes_preferences_on_a_copy(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    workflow = make_workflow(config, gateway).workflow
    session = workflow.load(SyncOptions(), GoogleCalendarSource(gateway, "Src"), "Tgt")

    plan = workflow.preview(session)

    colors = {m.summary: m.body["colorId"] for m in plan.mutations}
    assert colors == {"CS": "3", "Esame: CS": "11"}
    assert session.preferences == Preferences()  # untouched
    assert gateway.batches == []
    assert saved(config, "courses") == {}


def test_complete_save_and_apply_a_preview(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    workflow = make_workflow(config, gateway).workflow
    session = workflow.load(SyncOptions(), GoogleCalendarSource(gateway, "Src"), "Tgt")
    session.preferences.set_course_color("CS", GoogleColor.BASIL)
    plan = workflow.preview(session)

    workflow.complete_preferences(session)
    workflow.save_preferences(session)
    result = workflow.apply(session, plan)

    assert result.inserted == 2
    assert workflow.preview(session).is_empty
    assert saved(config, "courses") == {"CS": "10"}
    assert saved(config, "exams") == {
        "CS (2027-01-20)": {"color": "11", "subscribed": True}
    }


def test_load_discovers_with_a_given_profile(config: Config) -> None:
    save_profile(config, courses={"CS": "3"})
    gateway = FakeCalendarGateway({"Src": SOURCE})
    workflow = make_workflow(config, gateway).workflow
    profile = Profile(rules=[])  # e.g. edited in the TUI, not saved

    session = workflow.load(
        SyncOptions(), GoogleCalendarSource(gateway, "Src"), "Tgt", profile
    )

    assert session.profile is profile
    assert session.catalog == Catalog()


def test_profile_and_calendar_list(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    gateway.read_only = {"Src"}
    workflow = make_workflow(config, gateway).workflow

    profile = workflow.load_profile()
    profile.calendars = CalendarSettings(source="Src", target="Tgt")
    workflow.save_profile(profile)

    assert saved(config, "calendars") == {
        "source": "Src",
        "target": "Tgt",
        "time_zone": None,
    }
    assert workflow.list_calendars() == [
        CalendarInfo("id::Src", "Src", writable=False),
        CalendarInfo("id::Tgt", "Tgt", writable=True),
    ]


def test_rediscover_uses_the_current_rules(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    workflow = make_workflow(config, gateway).workflow
    session = workflow.load(SyncOptions(), GoogleCalendarSource(gateway, "Src"), "T")
    assert session.catalog.courses == ("CS",)

    session.profile.rules = []
    rediscovered = workflow.rediscover(session)

    assert rediscovered.catalog == Catalog()
    assert rediscovered.profile is session.profile


def test_syncable_events_leave_out_pruned_ones(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    workflow = make_workflow(config, gateway).workflow
    options = SyncOptions(prune_before=date(2027, 1, 1))
    session = workflow.load(options, GoogleCalendarSource(gateway, "Src"), "T")

    assert [e["id"] for e in workflow.syncable_events(session)] == ["eam00001"]


@pytest.mark.parametrize("time_zone", [None, "Asia/Tokyo"])
def test_new_target_calendar_gets_the_profile_time_zone(
    config: Config, time_zone: str | None
) -> None:
    save_profile(config, calendars={"time_zone": time_zone})
    gateway = FakeCalendarGateway({"Src": SOURCE})

    make_workflow(config, gateway).run(SyncOptions(), "Src", "Tgt")

    assert gateway.time_zones == {"Tgt": time_zone}


def test_sync_options_with_semester_default() -> None:
    # Mar 10, 2026 -> semester 2 (2026-03-01 to 2026-09-15)
    opts = SyncOptions.with_semester_default(now=date(2026, 3, 10))
    assert opts.window_from == date(2026, 3, 1)
    assert opts.window_to == date(2026, 9, 15)
    assert not opts.all_time

    # all_time overrides semester defaults
    opts_all = SyncOptions.with_semester_default(all_time=True, now=date(2026, 3, 10))
    assert opts_all.window_from is None
    assert opts_all.window_to is None
    assert opts_all.all_time

    # explicit window override
    opts_custom = SyncOptions.with_semester_default(
        window_from=date(2026, 4, 1),
        window_to=date(2026, 5, 1),
        now=date(2026, 3, 10),
    )
    assert opts_custom.window_from == date(2026, 4, 1)
    assert opts_custom.window_to == date(2026, 5, 1)


def test_workflow_course_filter(config: Config) -> None:
    source_events = [
        {
            "id": "cs1",
            "summary": "Lezione: Didattica - COMPUTER SCIENCE",
            "start": {"date": "2026-10-01"},
            "end": {"date": "2026-10-02"},
        },
        {
            "id": "math1",
            "summary": "Lezione: Didattica - MATHEMATICS",
            "start": {"date": "2026-10-01"},
            "end": {"date": "2026-10-02"},
        },
    ]
    gateway = FakeCalendarGateway({"Src": source_events, "Tgt": []})
    opts = SyncOptions(all_time=True, course="math")
    outcome = make_workflow(config, gateway).run(opts, "Src", "Tgt")
    assert outcome.result is not None
    assert outcome.result.inserted == 1
    [inserted] = gateway.events_of("Tgt")
    assert "MATHEMATICS" in inserted["summary"]


def test_workflow_course_filter_handles_unclassified_event(config: Config) -> None:
    source_events = [
        {
            "id": "unclassified",
            "summary": "Random event with no course or university pattern",
            "start": {"date": "2026-10-01"},
            "end": {"date": "2026-10-02"},
        },
        {
            "id": "math1",
            "summary": "Lezione: Didattica - MATHEMATICS",
            "start": {"date": "2026-10-01"},
            "end": {"date": "2026-10-02"},
        },
    ]
    gateway = FakeCalendarGateway({"Src": source_events, "Tgt": []})
    opts = SyncOptions(all_time=True, course="math")
    outcome = make_workflow(config, gateway).run(opts, "Src", "Tgt")
    assert outcome.result is not None
    assert outcome.result.inserted == 1
    [inserted] = gateway.events_of("Tgt")
    assert "MATHEMATICS" in inserted["summary"]
