import json
from unittest.mock import MagicMock

import pytest
from conftest import FakeCalendarGateway

from polimi_calendar_coloring.catalog import Catalog
from polimi_calendar_coloring.config import Config
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.preferences import JsonPreferencesRepository, Preferences
from polimi_calendar_coloring.reporting import NullReporter
from polimi_calendar_coloring.sync import (
    GoogleCalendarSource,
    SourceCalendarNotFoundError,
    SyncService,
)
from polimi_calendar_coloring.targets import SyncTarget
from polimi_calendar_coloring.workflow import SyncOptions, SyncWorkflow

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
        repo = JsonPreferencesRepository(
            config.course_colors_path,
            config.exam_states_path,
            config.deadline_colors_path,
        )
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
    assert json.loads(config.course_colors_path.read_text()) == {"CS": "3"}
    assert json.loads(config.exam_states_path.read_text()) == {
        "Esame: CS (2027-01-20)": {"color": "11", "subscribed": True}
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
    assert not config.course_colors_path.exists()
    assert not config.exam_states_path.exists()


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
    assert json.loads(config.course_colors_path.read_text()) == {"CS": "10"}
    assert not config.exam_states_path.exists()


def test_interactive_without_editor_is_a_programming_error(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    with pytest.raises(ValueError):
        make_workflow(config, gateway).run(SyncOptions(interactive=True), "Src", "Tgt")


def test_missing_source_raises_before_touching_anything(config: Config) -> None:
    gateway = FakeCalendarGateway()
    with pytest.raises(SourceCalendarNotFoundError):
        make_workflow(config, gateway).run(SyncOptions(), "Src", "Tgt")
    assert gateway.created == []
    assert not config.course_colors_path.exists()


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
    assert [e.title for e in catalog.exams] == ["Esame: CS"]


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
    assert json.loads(config.deadline_colors_path.read_text()) == {
        "Esame di laurea": event["colorId"]
    }
    assert not config.course_colors_path.exists()


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
    assert not config.course_colors_path.exists()


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
    assert json.loads(config.course_colors_path.read_text()) == {"CS": "10"}
    assert json.loads(config.exam_states_path.read_text()) == {
        "Esame: CS (2027-01-20)": {"color": "11", "subscribed": True}
    }
