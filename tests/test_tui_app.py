import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest
from conftest import FakeCalendarGateway

pytest.importorskip("textual")

from textual.pilot import Pilot
from textual.widgets import (
    Button,
    DataTable,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
)

from polimi_calendar_coloring.cli.main import build_workflow
from polimi_calendar_coloring.config import Config
from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.suggestions import suggest_color
from polimi_calendar_coloring.sync.source import GoogleCalendarSource
from polimi_calendar_coloring.targets import SyncTarget
from polimi_calendar_coloring.tui.app import PolimiCalendarApp, TuiReporter
from polimi_calendar_coloring.tui.widgets import ColorPicker, PlanTree
from polimi_calendar_coloring.workflow import SyncOptions

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

Scenario = Callable[[PolimiCalendarApp, Pilot[None]], Awaitable[None]]


def make_app(
    config: Config,
    gateway: FakeCalendarGateway,
    options: SyncOptions | None = None,
) -> PolimiCalendarApp:
    reporter = TuiReporter()
    return PolimiCalendarApp(
        build_workflow(config, gateway, reporter),
        GoogleCalendarSource(gateway, "Src"),
        options or SyncOptions(),
        "Tgt",
        reporter,
    )


def drive(app: PolimiCalendarApp, scenario: Scenario) -> None:
    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await scenario(app, pilot)

    asyncio.run(main())


async def settle(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def summary_text(app: PolimiCalendarApp) -> str:
    return str(app.query_one("#plan-summary", Static).render())


def cells(app: PolimiCalendarApp, tab: str, key: str) -> list[str]:
    table = app.query_one(f"#{tab}-table", DataTable)
    return [cell.plain for cell in table.get_row(key)]


async def log_text(app: PolimiCalendarApp, pilot: Pilot[None]) -> str:
    """The log is only rendered once its (Sync) tab is visible."""
    app.query_one(TabbedContent).active = "sync"
    await pilot.pause()
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


def test_lists_courses_exams_and_deadlines(config: Config) -> None:
    config.course_colors_path.write_text(json.dumps({"CS": "10"}))
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        assert cells(app, "courses", "CS")[1:] == ["    Basil", "saved"]
        math = suggest_color("Math").label
        assert cells(app, "courses", "Math")[1:] == [f"    {math}", "suggested"]
        assert cells(app, "exams", "Esame: CS (2027-01-20)") == [
            "Esame: CS",
            "2027-01-20",
            "Iscritto",
            "✔ yes",
            "    Tomato",
            "suggested",
        ]
        assert cells(app, "exams", "Esame: Physics (2027-02-01)")[3:] == [
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        tabs = app.query_one(TabbedContent)
        assert [pane.id for pane in tabs.query("TabPane")] == ["exams", "sync"]
        assert app.check_action("toggle_subscription", ()) is True

    drive(app, scenario)


def test_pick_a_color_and_save(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        await pilot.press("c")
        assert isinstance(app.screen, ColorPicker)
        assert app.screen.subject == "CS"
        await pilot.press("home", "enter")  # Lavender
        await pilot.pause()

        assert cells(app, "courses", "CS")[1:] == ["    Lavender", "● modified"]
        assert app.sub_title.endswith("• unsaved changes")
        assert not config.course_colors_path.exists()

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert json.loads(config.course_colors_path.read_text()) == {"CS": "1"}
        assert cells(app, "courses", "CS")[2] == "saved"
        assert app.sub_title == "'Src' ➔ 'Tgt'"

        await pilot.press("ctrl+s")  # nothing left to save
        await pilot.pause()

    drive(app, scenario)


def test_enter_opens_the_picker_and_escape_cancels(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        app.query_one(TabbedContent).active = "exams"
        await pilot.pause()
        app.query_one("#exams-table").focus()
        await pilot.press("down", "space")
        await pilot.pause()
        physics = "Esame: Physics (2027-02-01)"
        assert cells(app, "exams", physics)[3:] == ["✔ yes", "    Tomato", "● modified"]

        await pilot.press("c")
        assert isinstance(app.screen, ColorPicker)
        assert app.screen.subject == "Esame: Physics (2027-02-01)"
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
        assert json.loads(config.exam_states_path.read_text()) == {
            physics: {"color": "1", "subscribed": True}
        }
        assert json.loads(config.deadline_colors_path.read_text()) == {
            "Piano di studi": "11"
        }

    drive(app, scenario)


def test_preview_then_apply(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    app = make_app(config, gateway)

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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
        assert not config.course_colors_path.exists()

        await pilot.click("#apply")
        await settle(app, pilot)
        assert len(gateway.events_of("Tgt")) == 5
        assert "✔ Sync finished: 5 inserted, 0 updated, 0 deleted." in summary_text(app)
        assert app.plan is None
        assert json.loads(config.course_colors_path.read_text()).keys() == {
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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


def test_load_failure_is_reported(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway())

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        assert "Could not load the source events: Source calendar 'Src' not found." in (
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        app.workflow.save_preferences = denied  # type: ignore[method-assign]
        await pilot.press("c", "enter", "ctrl+s")
        await pilot.pause()
        assert "Could not save preferences: read-only" in await log_text(app, pilot)
        assert app.draft is not None and app.draft.is_dirty

    drive(app, scenario)


def test_quit_asks_to_confirm_unsaved_changes(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))
    exits: list[bool] = []

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        await pilot.press("q")
        await pilot.pause()

    drive(app, scenario)
    assert app.return_code == 0


def test_reporter_messages_reach_the_log(config: Config) -> None:
    config.course_colors_path.write_text("{not json")
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: PolimiCalendarApp, pilot: Pilot[None]) -> None:
        reporter = app.workflow.reporter
        reporter.detail("a detail")
        reporter.dry_run_finished(None)  # type: ignore[arg-type]
        await pilot.pause()
        text = await log_text(app, pilot)
        assert "is corrupted. Starting fresh." in text
        assert "a detail" in text

    drive(app, scenario)


def test_reporter_without_app_drops_messages() -> None:
    TuiReporter().info("nobody listens")
