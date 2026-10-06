import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import FakeCalendarGateway, save_profile, saved

pytest.importorskip("textual")

from textual.pilot import Pilot
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    OptionList,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
)

from calendar_coloring.cli.main import build_workflow
from calendar_coloring.config import Config
from calendar_coloring.palette import GoogleColor
from calendar_coloring.profile import CalendarSettings
from calendar_coloring.suggestions import suggest_color
from calendar_coloring.sync.source import GoogleCalendarSource
from calendar_coloring.targets import SyncTarget
from calendar_coloring.tui.app import CalendarColoringApp, TuiReporter
from calendar_coloring.tui.setup import CalendarSetup, Role
from calendar_coloring.tui.widgets import CalendarPicker, ColorPicker, PlanTree
from calendar_coloring.workflow import SyncOptions

SOURCE = [
    {
        "id": "lec00001",
        "summary": "Lezione: Didattica - CS",
        "start": {"dateTime": "2026-09-20T10:00:00+02:00"},
        "end": {"dateTime": "2026-09-20T12:00:00+02:00"},
        "location": "Aula 1",
    },
    {
        "id": "lec00002",
        "summary": "Lezione: Didattica - Math",
        "start": {"date": "2026-09-21"},
        "end": {"date": "2026-09-22"},
    },
    {
        "id": "eam00001",
        "summary": "Esame: CS",
        "description": "Iscritto",
        "start": {"date": "2027-01-20"},
        "end": {"date": "2027-01-21"},
    },
    {
        "id": "eam00002",
        "summary": "Esame: Physics",
        "start": {"date": "2027-02-01"},
        "end": {"date": "2027-02-02"},
    },
    {
        "id": "dead0001",
        "summary": "Scadenza: Piano di studi",
        "start": {"date": "2026-11-15"},
        "end": {"date": "2026-11-16"},
    },
]

Scenario = Callable[[CalendarColoringApp, Pilot[None]], Awaitable[None]]


def make_app(
    config: Config,
    gateway: FakeCalendarGateway,
    options: SyncOptions | None = None,
    setup: CalendarSetup | None = None,
) -> CalendarColoringApp:
    reporter = TuiReporter()
    return CalendarColoringApp(
        build_workflow(config, gateway, reporter),
        setup or make_setup(gateway),
        options or SyncOptions(),
        reporter,
    )


def make_setup(gateway: FakeCalendarGateway, **kwargs: Any) -> CalendarSetup:
    return CalendarSetup(
        calendars=CalendarSettings(source="Src", target="Tgt"),
        source_for=lambda name: GoogleCalendarSource(gateway, name),
        **kwargs,
    )


def drive(app: CalendarColoringApp, scenario: Scenario) -> None:
    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await scenario(app, pilot)

    asyncio.run(main())


async def settle(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def summary_text(app: CalendarColoringApp) -> str:
    return str(app.query_one("#plan-summary", Static).render())


def cells(app: CalendarColoringApp, tab: str, key: str) -> list[str]:
    table = app.query_one(f"#{tab}-table", DataTable)
    return [cell.plain for cell in table.get_row(key)]


async def log_text(app: CalendarColoringApp, pilot: Pilot[None]) -> str:
    """The log is only rendered once its (Sync) tab is visible."""
    app.query_one(TabbedContent).active = "sync"
    await pilot.pause()
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


def test_lists_courses_exams_and_deadlines(config: Config) -> None:
    save_profile(config, courses={"CS": "10"})
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert cells(app, "courses", "CS")[1:] == ["    Basil", "saved"]
        math = suggest_color("Math").label
        assert cells(app, "courses", "Math")[1:] == [f"    {math}", "suggested"]
        assert cells(app, "exams", "CS (2027-01-20)") == [
            "CS",
            "2027-01-20",
            "enrolled",
            "✔ yes",
            "    Tomato",
            "suggested",
        ]
        assert cells(app, "exams", "Physics (2027-02-01)")[3:] == [
            "?",
            "   — none",
            "not set",
        ]
        assert cells(app, "deadlines", "Piano di studi")[2] == "suggested"
        assert app.sub_title == "'Src' ➔ 'Tgt'"
        assert app.focused is app.query_one("#courses-table")
        assert "Loading events from 'Src'..." in await log_text(app, pilot)

    drive(app, scenario)


def test_target_selects_the_tabs(config: Config) -> None:
    app = make_app(
        config,
        FakeCalendarGateway({"Src": SOURCE}),
        SyncOptions(target=SyncTarget.EXAMS),
    )

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        tabs = app.query_one(TabbedContent)
        assert [pane.id for pane in tabs.query("TabPane")] == ["exams", "sync", "setup"]
        assert app.check_action("toggle_subscription", ()) is True

    drive(app, scenario)


def test_pick_a_color_and_save(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("c")
        assert isinstance(app.screen, ColorPicker)
        assert app.screen.subject == "CS"
        await pilot.press("home", "enter")  # Lavender
        await pilot.pause()

        assert cells(app, "courses", "CS")[1:] == ["    Lavender", "● modified"]
        assert app.sub_title.endswith("• unsaved changes")
        assert saved(config, "courses") == {}

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert saved(config, "courses") == {"CS": "1"}
        assert cells(app, "courses", "CS")[2] == "saved"
        assert app.sub_title == "'Src' ➔ 'Tgt'"

        await pilot.press("ctrl+s")  # nothing left to save
        await pilot.pause()

    drive(app, scenario)


def test_enter_opens_the_picker_and_escape_cancels(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("down", "enter")
        assert isinstance(app.screen, ColorPicker)
        assert app.screen.subject == "Math"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ColorPicker)
        assert app.draft is not None and not app.draft.is_dirty

    drive(app, scenario)


def test_edit_exams_and_deadlines(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.query_one(TabbedContent).active = "exams"
        await pilot.pause()
        app.query_one("#exams-table").focus()
        await pilot.press("down", "space")
        await pilot.pause()
        physics = "Physics (2027-02-01)"
        assert cells(app, "exams", physics)[3:] == ["✔ yes", "    Tomato", "● modified"]

        await pilot.press("c")
        assert isinstance(app.screen, ColorPicker)
        assert app.screen.subject == "Physics (2027-02-01)"
        await pilot.press("home", "enter")
        await pilot.pause()
        assert cells(app, "exams", physics)[4] == "    Lavender"

        app.query_one(TabbedContent).active = "deadlines"
        await pilot.pause()
        assert app.check_action("toggle_subscription", ()) is False
        await pilot.press("c", "end", "enter")
        await pilot.pause()
        assert cells(app, "deadlines", "Piano di studi")[1] == "    Tomato"

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert saved(config, "exams") == {physics: {"color": "1", "subscribed": True}}
        assert saved(config, "deadlines") == {"Piano di studi": "11"}

    drive(app, scenario)


def test_preview_then_apply(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.query_one("#apply", Button).disabled
        assert app.check_action("apply", ()) is None

        await pilot.press("p")
        await settle(app, pilot)
        assert app.query_one(TabbedContent).active == "sync"
        summary = summary_text(app)
        assert "+ 5 insert" in summary
        assert "The target calendar will be created." in summary
        tree = app.query_one(PlanTree)
        [inserts] = tree.root.children
        assert str(inserts.label) == "+ Insert (5)"
        cs = inserts.children[0]
        assert str(cs.label) == "CS"
        details = [str(child.label) for child in cs.children]
        assert (
            details[1] == "When: 2026-09-20T10:00:00+02:00 → 2026-09-20T12:00:00+02:00"
        )
        assert "Source title: Lezione: Didattica - CS" in details
        assert "Location: Aula 1" in details
        assert gateway.batches == []
        assert saved(config, "courses") == {}

        await pilot.click("#apply")
        await settle(app, pilot)
        assert len(gateway.events_of("Tgt")) == 5
        assert "✔ Sync finished: 5 inserted, 0 updated, 0 deleted." in summary_text(app)
        assert app.plan is None
        assert saved(config, "courses").keys() == {
            "CS",
            "Math",
        }
        assert cells(app, "courses", "CS")[2] == "saved"
        assert "Executing 5 calendar operation(s)..." in await log_text(app, pilot)

        await pilot.click("#preview")
        await settle(app, pilot)
        assert app.plan is not None and app.plan.is_empty
        assert "· 5 unchanged" in summary_text(app)

    drive(app, scenario)


def test_plan_tree_shows_updates_and_deletes(config: Config) -> None:
    stale = {
        "id": "old00001",
        "summary": "Old",
        "start": {"date": "2026-01-01"},
        "extendedProperties": {"private": {"polimi_sync_managed": "true"}},
    }
    outdated = {**SOURCE[1], "summary": "Math (old title)", "recurrence": None}
    gateway = FakeCalendarGateway({"Src": SOURCE[1:2], "Tgt": [stale, outdated]})
    app = make_app(config, gateway, SyncOptions(target=SyncTarget.LECTURES))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("p")
        await settle(app, pilot)
        tree = app.query_one(PlanTree)
        labels = [str(group.label) for group in tree.root.children]
        assert labels == ["~ Update (1)", "- Delete (1)"]
        assert str(tree.root.children[1].children[0].label) == "Old"
        summary = summary_text(app)
        assert "~ 1 update" in summary
        assert "left untouched" not in summary

    drive(app, scenario)


def test_editing_discards_a_stale_preview(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("p")
        await settle(app, pilot)
        assert app.plan is not None
        app.query_one(TabbedContent).active = "courses"
        await pilot.pause()
        await pilot.press("c", "enter")
        await pilot.pause()
        assert app.plan is None
        assert app.query_one("#apply", Button).disabled
        assert "preview the changes again" in summary_text(app)

    drive(app, scenario)


def test_apply_reports_progress_and_failures(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    gateway.fail_event_ids = {"lec00001"}
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        progress = app.query_one(ProgressBar)
        assert (progress.progress, progress.total) == (5, 5)
        assert not progress.display
        assert "1 failure(s)" in summary_text(app)
        assert "Failed to insert 'CS': boom" in await log_text(app, pilot)

    drive(app, scenario)


def test_missing_source_calendar_opens_the_setup(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway())

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        assert app.query_one(TabbedContent).active == "setup"
        assert not app.query_one("#change-source", Button).disabled
        assert (
            "Source calendar 'Src' not found. Choose it in the Setup tab."
            in await log_text(app, pilot)
        )

    drive(app, scenario)


def test_load_failure_is_reported(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network down")

    gateway.get_all_events = boom  # type: ignore[method-assign]
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        assert "Could not load the source events: network down" in (
            await log_text(app, pilot)
        )
        assert app.query_one("#preview", Button).disabled
        assert app.check_action("pick_color", ()) is False
        assert app.check_action("save", ()) is False
        assert app.check_action("preview", ()) is None
        await pilot.press("c", "space", "ctrl+s", "p", "a")
        await pilot.pause()
        app.action_save()
        app.action_toggle_subscription()
        app._pick_color("courses", "CS")
        app._color_chosen("courses", "CS", GoogleColor.BASIL)
        assert len(app.screen_stack) == 1

    drive(app, scenario)


def test_preview_and_apply_failures_are_reported(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    def boom(*args: object) -> None:
        raise RuntimeError("network down")

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        preview = app.workflow.preview
        app.workflow.preview = boom  # type: ignore[method-assign]
        await pilot.press("p")
        await settle(app, pilot)
        assert "Could not compute the changes: network down" in await log_text(
            app, pilot
        )
        assert app.plan is None

        app.workflow.preview = preview  # type: ignore[method-assign]
        await pilot.press("p")
        await settle(app, pilot)
        app.workflow.apply = boom  # type: ignore[method-assign]
        await pilot.press("a")
        await settle(app, pilot)
        assert "Could not apply the changes: network down" in await log_text(app, pilot)
        assert not app.busy

    drive(app, scenario)


def test_save_failure_is_reported(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    def denied(*args: object) -> None:
        raise PermissionError("read-only")

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.workflow.save_preferences = denied  # type: ignore[method-assign]
        await pilot.press("c", "enter", "ctrl+s")
        await pilot.pause()
        assert "Could not save preferences: read-only" in await log_text(app, pilot)
        assert app.draft is not None and app.draft.is_dirty

    drive(app, scenario)


def test_quit_asks_to_confirm_unsaved_changes(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))
    exits: list[bool] = []

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.exit = lambda *args, **kwargs: exits.append(True)  # type: ignore[method-assign]
        await pilot.press("c", "enter")
        await pilot.press("q")
        await pilot.pause()
        assert exits == []
        await pilot.press("q")
        await pilot.pause()
        assert exits == [True]

    drive(app, scenario)


def test_quit_without_changes_exits_immediately(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pilot.press("q")
        await pilot.pause()

    drive(app, scenario)
    assert app.return_code == 0


def test_reporter_messages_reach_the_log(config: Config) -> None:
    config.profile_path.write_text("{not json")
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        reporter = app.workflow.reporter
        reporter.detail("a detail")
        reporter.dry_run_finished(None)  # type: ignore[arg-type]
        await pilot.pause()
        text = await log_text(app, pilot)
        assert "corrupted. Starting fresh." in text
        assert "a detail" in text

    drive(app, scenario)


def test_reporter_without_app_drops_messages() -> None:
    TuiReporter().info("nobody listens")


# -- setup --------------------------------------------------------------------


async def open_picker(
    app: CalendarColoringApp, pilot: Pilot[None], role: str
) -> CalendarPicker:
    app.query_one(TabbedContent).active = "setup"
    await pilot.pause()
    await pilot.click(f"#change-{role}")
    await settle(app, pilot)
    assert isinstance(app.screen, CalendarPicker)
    return app.screen


async def pick_calendar(
    app: CalendarColoringApp, pilot: Pilot[None], role: str, name: str
) -> None:
    picker = await open_picker(app, pilot, role)
    picker.query_one(OptionList).highlighted = [c.name for c in picker.choices].index(
        name
    )
    await pilot.press("enter")
    await settle(app, pilot)


def text_of(app: CalendarColoringApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


def test_setup_shows_the_calendars_and_overrides(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    setup = make_setup(gateway, overrides={Role.TARGET: "TARGET_CALENDAR_NAME"})
    app = make_app(config, gateway, setup=setup)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.query_one(TabbedContent).active = "setup"
        await pilot.pause()
        assert text_of(app, "#setup-source") == "'Src'"
        assert text_of(app, "#setup-target") == "'Tgt'"
        assert "TARGET_CALENDAR_NAME is set" in text_of(app, "#setup-notes")
        assert app.sub_title == "'Src' ➔ 'Tgt'"

    drive(app, scenario)


def test_change_the_target_to_an_existing_calendar(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": [], "Other": []})
    gateway.read_only = {"Src"}
    save_profile(config, calendars={"source": "Src", "target": "Tgt"})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.draft is not None
        app.draft.set_course_color("CS", GoogleColor.BASIL)
        app._preferences_changed()
        app.action_preview()
        await settle(app, pilot)
        assert app.plan is not None

        picker = await open_picker(app, pilot, "target")
        assert [(c.name, c.allowed) for c in picker.choices] == [
            ("Src", False),
            ("Tgt", True),
            ("Other", True),
        ]
        await pilot.press("escape")
        await pilot.pause()
        await pick_calendar(app, pilot, "target", "Other")

        assert app.calendars.target == "Other"
        assert app.plan is None
        assert "'Src' ➔ 'Other'" in app.sub_title
        assert text_of(app, "#setup-target") == "'Other'"
        # Saved right away, without the unsaved course color.
        assert saved(config, "calendars") == {"source": "Src", "target": "Other"}
        assert saved(config, "courses") == {}
        assert app.draft.is_dirty

        app.action_preview()
        await settle(app, pilot)
        app.action_apply()
        await settle(app, pilot)
        assert len(gateway.events_of("Other")) == len(SOURCE)
        assert gateway.events_of("Tgt") == []

    drive(app, scenario)


def test_a_new_target_calendar_is_created_on_apply(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        picker = await open_picker(app, pilot, "target")
        field = picker.query_one(Input)
        field.focus()
        field.value = "Src"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is picker
        assert "can't be the source" in str(
            picker.query_one("#calendar-error", Label).render()
        )

        field.value = "  Brand new  "
        await pilot.press("enter")
        await settle(app, pilot)
        assert app.calendars.target == "Brand new"

        app.action_preview()
        await settle(app, pilot)
        assert "The target calendar will be created." in summary_text(app)
        app.action_apply()
        await settle(app, pilot)
        assert gateway.created == ["Brand new"]

    drive(app, scenario)


def test_picking_the_current_calendar_changes_nothing(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pick_calendar(app, pilot, "target", "Tgt")
        await pick_calendar(app, pilot, "source", "Src")
        assert app.calendars == CalendarSettings(source="Src", target="Tgt")
        assert not config.profile_path.exists()

    drive(app, scenario)


def test_change_the_source_reloads_and_keeps_unsaved_edits(config: Config) -> None:
    other = [
        {
            "id": "bio00001",
            "summary": "Lezione: Didattica - Bio",
            "start": {"date": "2026-09-22"},
            "end": {"date": "2026-09-23"},
        }
    ]
    gateway = FakeCalendarGateway({"Src": SOURCE, "Other": other})
    save_profile(config, calendars={"source": "Src", "target": "Tgt"})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.draft is not None
        app.draft.set_course_color("CS", GoogleColor.BASIL)
        app._preferences_changed()

        await pick_calendar(app, pilot, "source", "Other")

        assert app.calendars.source == "Other"
        assert app.sub_title.startswith("'Other' ➔ 'Tgt'")
        assert app.query_one("#courses-table", DataTable).get_row("Bio")
        assert app.draft.preferences.course_color("CS") is GoogleColor.BASIL
        assert app.draft.is_dirty
        assert saved(config, "calendars") == {"source": "Other", "target": "Tgt"}
        assert saved(config, "courses") == {}

    drive(app, scenario)


def test_a_source_that_fails_to_load_is_not_kept(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Other": []})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("network down")

        gateway.get_all_events = boom  # type: ignore[method-assign]
        await pick_calendar(app, pilot, "source", "Other")

        assert app.calendars.source == "Src"
        assert app.draft is not None
        assert app.query_one("#courses-table", DataTable).get_row("CS")
        assert not config.profile_path.exists()
        assert "Could not load the source events: network down" in (
            await log_text(app, pilot)
        )

    drive(app, scenario)


def test_choose_the_source_when_it_is_missing(config: Config) -> None:
    gateway = FakeCalendarGateway({"Uni": SOURCE})
    save_profile(config, calendars={"source": "Src", "target": "Tgt"})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        await pick_calendar(app, pilot, "source", "Uni")

        assert app.draft is not None
        assert app.calendars.source == "Uni"
        assert saved(config, "calendars") == {"source": "Uni", "target": "Tgt"}
        assert not app.draft.is_dirty

    drive(app, scenario)


def test_only_the_chosen_calendar_is_saved_over_the_profile(config: Config) -> None:
    # The app starts from "Src", e.g. set by SOURCE_CALENDAR_NAME: the profile
    # keeps its own source. This also works before any source loaded.
    save_profile(config, calendars={"source": "Uni", "target": "Tgt"})
    gateway = FakeCalendarGateway({"Uni": SOURCE})
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        picker = await open_picker(app, pilot, "target")
        picker.query_one(Input).value = "Mine"
        picker.query_one(Input).focus()
        await pilot.press("enter")
        await settle(app, pilot)

        assert app.draft is None
        assert saved(config, "calendars") == {"source": "Uni", "target": "Mine"}

    drive(app, scenario)


def test_calendar_list_failure_is_reported(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})

    def boom() -> None:
        raise RuntimeError("offline")

    gateway.list_calendars = boom  # type: ignore[assignment,method-assign]
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.query_one(TabbedContent).active = "setup"
        await pilot.pause()
        await pilot.click("#change-source")
        await settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert "Could not list your calendars: offline" in await log_text(app, pilot)

    drive(app, scenario)


def test_profile_save_failure_on_calendar_change(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Other": []})
    app = make_app(config, gateway)

    def boom(profile: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(app.workflow, "save_profile", boom)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        await pick_calendar(app, pilot, "target", "Other")
        assert app.calendars.target == "Other"
        assert "Could not save the profile: read-only file system" in (
            await log_text(app, pilot)
        )

    drive(app, scenario)


def test_an_ical_source_cannot_be_changed(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    setup = make_setup(gateway, fixed_source="iCal feed at https://x/<redacted>")
    app = make_app(config, gateway, setup=setup)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        app.query_one(TabbedContent).active = "setup"
        await pilot.pause()
        assert app.query_one("#change-source", Button).disabled
        assert not app.query_one("#change-target", Button).disabled
        assert text_of(app, "#setup-source") == "iCal feed at https://x/<redacted>"
        assert "iCal feed" in text_of(app, "#setup-notes")

    drive(app, scenario)


def test_target_picker_without_calendars_asks_for_a_name(config: Config) -> None:
    gateway = FakeCalendarGateway()
    app = make_app(config, gateway)

    async def scenario(app: CalendarColoringApp, pilot: Pilot[None]) -> None:
        picker = await open_picker(app, pilot, "target")
        assert not picker.query(OptionList)
        assert picker.query_one(Input).has_focus
        await pilot.press(*"Mine", "enter")
        await settle(app, pilot)
        assert app.calendars.target == "Mine"

    drive(app, scenario)
