import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import FakeCalendarGateway, save_profile, saved

pytest.importorskip("textual")

from textual.pilot import Pilot
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
    TabbedContent,
)

from unical.cli.main import build_workflow
from unical.config import Config
from unical.palette import GoogleColor
from unical.profile import CalendarSettings
from unical.rules import EventKind, MatchKind
from unical.suggestions import suggest_color
from unical.sync.models import Mutation, MutationAction, SyncPlan
from unical.sync.source import GoogleCalendarSource
from unical.targets import SyncTarget
from unical.tui.app import TuiReporter, UnicalApp
from unical.tui.setup import CalendarSetup, Role
from unical.tui.widgets import (
    CalendarPicker,
    ColorPicker,
    PlanTree,
    RuleEditor,
    SyncProgress,
    TextPrompt,
    progress_text,
)
from unical.tui.wizard import WizardIntro, WizardRules
from unical.workflow import SyncOptions

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

Scenario = Callable[[UnicalApp, Pilot[None]], Awaitable[None]]


def make_app(
    config: Config,
    gateway: FakeCalendarGateway,
    options: SyncOptions | None = None,
    setup: CalendarSetup | None = None,
) -> UnicalApp:
    reporter = TuiReporter()
    return UnicalApp(
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


def drive(app: UnicalApp, scenario: Scenario) -> None:
    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(app, pilot)
            await scenario(app, pilot)

    asyncio.run(main())


async def settle(app: UnicalApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def summary_text(app: UnicalApp) -> str:
    return str(app.query_one("#plan-summary", Static).render())


def cells(app: UnicalApp, tab: str, key: str) -> list[str]:
    table = app.query_one(f"#{tab}-table", DataTable)
    return [cell.plain for cell in table.get_row(key)]


async def show_tab(app: UnicalApp, pilot: Pilot[None], tab: str) -> None:
    """The app opens on the Setup tab: goes to a table and focuses it."""
    app.query_one(TabbedContent).active = tab
    await pilot.pause()
    app.query_one(f"#{tab}-table").focus()
    await pilot.pause()


async def log_text(app: UnicalApp, pilot: Pilot[None]) -> str:
    """The log is only rendered once its (Sync) tab is visible."""
    app.query_one(TabbedContent).active = "sync"
    await pilot.pause()
    return "\n".join(line.text for line in app.query_one(RichLog).lines)


def test_lists_courses_exams_and_deadlines(config: Config) -> None:
    save_profile(config, courses={"CS": "10"})
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert app.query_one(TabbedContent).active == "setup"
        await show_tab(app, pilot, "courses")
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


def visible_tabs(app: UnicalApp) -> list[str | None]:
    tabs = app.query_one(TabbedContent)
    return [pane.id for pane in tabs.query("TabPane") if tabs.get_tab(pane).display]


def test_target_selects_the_tabs(config: Config) -> None:
    app = make_app(
        config,
        FakeCalendarGateway({"Src": SOURCE}),
        SyncOptions(target=SyncTarget.EXAMS),
    )

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert visible_tabs(app) == ["setup", "exams", "sync", "rules"]
        await show_tab(app, pilot, "exams")
        assert app.query_one("#scope-sync", Select).value is SyncTarget.EXAMS
        assert app.query_one("#scope-setup", Select).value is SyncTarget.EXAMS
        assert app.check_action("toggle_subscription", ()) is True

    drive(app, scenario)


def planned(app: UnicalApp) -> list[str]:
    assert app.plan is not None
    return [m.summary for m in app.plan.mutations]


def test_the_sync_tab_limits_the_sync_to_lectures_exams_or_all(
    config: Config,
) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        scope = app.query_one("#scope-sync", Select)
        setup_scope = app.query_one("#scope-setup", Select)
        assert scope.value is SyncTarget.ALL
        await pilot.press("p")
        await settle(app, pilot)
        assert planned(app) == [
            "CS",
            "Math",
            "Esame: CS",
            "Esame: Physics",
            "Scadenza: Piano di studi",
        ]

        scope.value = SyncTarget.LECTURES
        await pilot.pause()
        assert app.options.target is SyncTarget.LECTURES
        assert setup_scope.value is SyncTarget.LECTURES  # kept in sync
        assert visible_tabs(app) == ["setup", "courses", "sync", "rules"]
        assert app.plan is None
        await pilot.press("p")
        await settle(app, pilot)
        assert planned(app) == ["CS", "Math"]

        setup_scope.value = SyncTarget.EXAMS  # chosen from the Setup tab
        await pilot.pause()
        assert scope.value is SyncTarget.EXAMS
        assert visible_tabs(app) == ["setup", "exams", "sync", "rules"]
        await pilot.press("p")
        await settle(app, pilot)
        assert planned(app) == ["Esame: CS", "Esame: Physics"]

        scope.value = SyncTarget.ALL
        await pilot.pause()
        assert visible_tabs(app) == [
            "setup",
            "courses",
            "exams",
            "deadlines",
            "sync",
            "rules",
        ]
        await pilot.press("p")
        await settle(app, pilot)
        assert len(planned(app)) == 5

    drive(app, scenario)


def test_pick_a_color_and_save(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await show_tab(app, pilot, "courses")
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await show_tab(app, pilot, "courses")
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        progress = app.query_one(SyncProgress)
        assert progress.completed == 5
        assert not progress.display
        assert "1 failure(s)" in summary_text(app)
        assert "Failed to insert 'CS': boom" in await log_text(app, pilot)

    drive(app, scenario)


PLAN = SyncPlan(
    target_calendar_id="tgt",
    mutations=(
        *(Mutation(MutationAction.INSERT, f"i{n}", "New") for n in range(4)),
        *(Mutation(MutationAction.UPDATE, f"u{n}", "Old") for n in range(4)),
        Mutation(MutationAction.DELETE, "d0", "Gone"),
        Mutation(MutationAction.DELETE, "d1", "Gone"),
    ),
)


def test_progress_text_counts_each_kind_of_change() -> None:
    text = progress_text(PLAN, completed=6, elapsed=3).plain
    assert "Writing changes..." in text
    assert "6/10  60%" in text
    assert "+ 4/4 insert  ~ 2/4 update  - 0/2 delete" in text
    assert "0:03 elapsed · about 0:02 left" in text


def test_progress_text_before_the_first_batch_is_indeterminate() -> None:
    text = progress_text(PLAN, completed=0, elapsed=0).plain
    assert "Writing the first changes..." in text
    assert "left" not in text
    assert "%" not in text
    new_calendar = SyncPlan(target_calendar_id=None, mutations=PLAN.mutations)
    assert "Creating the target calendar" in progress_text(new_calendar, 0, 0).plain


def test_progress_text_animates_and_finishes() -> None:
    bars = {progress_text(PLAN, 0, n / 12).plain.splitlines()[1] for n in range(40)}
    assert len(bars) > 10  # the pulse moves
    spinners = {progress_text(PLAN, 5, n / 12).plain[0] for n in range(10)}
    assert len(spinners) == 10
    done = progress_text(PLAN, 10, 7).plain
    assert "Wrapping up..." in done
    assert "left" not in done
    assert "+ 4/4 insert  ~ 4/4 update  - 2/2 delete" in done


def test_the_progress_is_shown_while_applying(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Tgt": []})
    app = make_app(config, gateway)
    halfway = threading.Event()
    resume = threading.Event()

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        apply = app.workflow.apply

        def slow_apply(session: Any, plan: Any, on_progress: Any) -> Any:
            def stalled(completed: int, total: int) -> None:
                on_progress(2, total)
                halfway.set()
                resume.wait(5)

            return apply(session, plan, stalled)

        app.workflow.apply = slow_apply  # type: ignore[method-assign]
        await pilot.press("p")
        await settle(app, pilot)
        progress = app.query_one(SyncProgress)
        assert not progress.display

        await pilot.press("a")
        await until(pilot, lambda: halfway.is_set() and progress.completed == 2)
        assert progress.display
        shown = str(progress.render())
        assert "Writing changes..." in shown
        assert "2/5  40%" in shown
        assert "+ 2/5 insert" in shown
        first_frame = shown.splitlines()[0]
        await until(
            pilot, lambda: str(progress.render()).splitlines()[0] != first_frame
        )

        resume.set()
        await settle(app, pilot)
        assert not progress.display
        assert "✔ Sync finished" in summary_text(app)

    drive(app, scenario)


def test_missing_source_calendar_opens_the_setup(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway())

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        app.workflow.save_preferences = denied  # type: ignore[method-assign]
        await show_tab(app, pilot, "courses")
        await pilot.press("c", "enter", "ctrl+s")
        await pilot.pause()
        assert "Could not save preferences: read-only" in await log_text(app, pilot)
        assert app.draft is not None and app.draft.is_dirty

    drive(app, scenario)


def test_quit_asks_to_confirm_unsaved_changes(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))
    exits: list[bool] = []

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        app.exit = lambda *args, **kwargs: exits.append(True)  # type: ignore[method-assign]
        await show_tab(app, pilot, "courses")
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await pilot.press("q")
        await pilot.pause()

    drive(app, scenario)
    assert app.return_code == 0


def test_reporter_messages_reach_the_log(config: Config) -> None:
    config.profile_path.write_text("{not json")
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        reporter = app.workflow.reporter
        reporter.detail("a detail")
        reporter.dry_run_finished(None)  # type: ignore[arg-type]
        await pilot.pause()
        text = await log_text(app, pilot)
        assert "corrupted. Starting fresh." in " ".join(text.split())
        assert "a detail" in text

    drive(app, scenario)


def test_reporter_without_app_drops_messages() -> None:
    TuiReporter().info("nobody listens")


# -- setup --------------------------------------------------------------------


async def open_picker(app: UnicalApp, pilot: Pilot[None], role: str) -> CalendarPicker:
    app.query_one(TabbedContent).active = "setup"
    await pilot.pause()
    await pilot.click(f"#change-{role}")
    await settle(app, pilot)
    assert isinstance(app.screen, CalendarPicker)
    return app.screen


async def pick_calendar(
    app: UnicalApp, pilot: Pilot[None], role: str, name: str
) -> None:
    picker = await open_picker(app, pilot, role)
    picker.query_one(OptionList).highlighted = [c.name for c in picker.choices].index(
        name
    )
    await pilot.press("enter")
    await settle(app, pilot)


def text_of(app: UnicalApp, selector: str) -> str:
    return str(app.query_one(selector, Static).render())


def test_setup_shows_the_calendars_and_overrides(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    setup = make_setup(gateway, overrides={Role.TARGET: "TARGET_CALENDAR_NAME"})
    app = make_app(config, gateway, setup=setup)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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
        assert saved(config, "calendars") == {
            "source": "Src",
            "target": "Other",
            "time_zone": None,
        }
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert app.draft is not None
        app.draft.set_course_color("CS", GoogleColor.BASIL)
        app._preferences_changed()

        await pick_calendar(app, pilot, "source", "Other")

        assert app.calendars.source == "Other"
        assert app.sub_title.startswith("'Other' ➔ 'Tgt'")
        assert app.query_one("#courses-table", DataTable).get_row("Bio")
        assert app.draft.preferences.course_color("CS") is GoogleColor.BASIL
        assert app.draft.is_dirty
        assert saved(config, "calendars") == {
            "source": "Other",
            "target": "Tgt",
            "time_zone": None,
        }
        assert saved(config, "courses") == {}

    drive(app, scenario)


def test_a_source_that_fails_to_load_is_not_kept(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE, "Other": []})
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        await pick_calendar(app, pilot, "source", "Uni")

        assert app.draft is not None
        assert app.calendars.source == "Uni"
        assert saved(config, "calendars") == {
            "source": "Uni",
            "target": "Tgt",
            "time_zone": None,
        }
        assert not app.draft.is_dirty

    drive(app, scenario)


def test_only_the_chosen_calendar_is_saved_over_the_profile(config: Config) -> None:
    # The app starts from "Src", e.g. set by SOURCE_CALENDAR_NAME: the profile
    # keeps its own source. This also works before any source loaded.
    save_profile(config, calendars={"source": "Uni", "target": "Tgt"})
    gateway = FakeCalendarGateway({"Uni": SOURCE})
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        picker = await open_picker(app, pilot, "target")
        picker.query_one(Input).value = "Mine"
        picker.query_one(Input).focus()
        await pilot.press("enter")
        await settle(app, pilot)

        assert app.draft is None
        assert saved(config, "calendars") == {
            "source": "Uni",
            "target": "Mine",
            "time_zone": None,
        }

    drive(app, scenario)


def test_calendar_list_failure_is_reported(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})

    def boom() -> None:
        raise RuntimeError("offline")

    gateway.list_calendars = boom  # type: ignore[assignment,method-assign]
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
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

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        picker = await open_picker(app, pilot, "target")
        assert not picker.query(OptionList)
        assert picker.query_one(Input).has_focus
        await pilot.press(*"Mine", "enter")
        await settle(app, pilot)
        assert app.calendars.target == "Mine"

    drive(app, scenario)


# -- rules --------------------------------------------------------------------

SEMINAR = {
    "id": "sem00001",
    "summary": "Seminario: AI Safety",
    "start": {"date": "2026-10-10"},
    "end": {"date": "2026-10-11"},
}


async def open_rules(
    app: UnicalApp, pilot: Pilot[None], table: str = "rules"
) -> DataTable[Any]:
    app.query_one(TabbedContent).active = "rules"
    await pilot.pause()
    widget = app.query_one(f"#{table}-table", DataTable)
    widget.focus()
    await pilot.pause()
    return widget


def row_texts(app: UnicalApp, table: str) -> list[list[str]]:
    widget = app.query_one(f"#{table}-table", DataTable)
    return [
        [cell.plain for cell in widget.get_row_at(index)]
        for index in range(widget.row_count)
    ]


def editor(app: UnicalApp) -> RuleEditor:
    assert isinstance(app.screen, RuleEditor)
    return app.screen


def status_of(form: RuleEditor) -> str:
    return str(form.query_one("#rule-status", Static).render())


def test_rules_tab_shows_rules_enrollment_and_preview(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": [*SOURCE, SEMINAR]}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await open_rules(app, pilot)
        assert row_texts(app, "rules")[0] == [
            "1",
            "Exam",
            "title starts with 'Esame: '",
            "{title}",
        ]
        assert len(row_texts(app, "rules")) == 6
        assert row_texts(app, "enrollment") == [
            ["Enrolled", "description starts with 'Iscritto'"],
            ["Not enrolled", "description starts with 'Non iscritto'"],
        ]
        assert text_of(app, "#rules-summary") == (
            "2 lectures · 2 exams · 1 deadlines · 1 unmatched"
        )
        assert row_texts(app, "events")[-1] == [
            "unmatched",
            "1",
            "Seminario: AI Safety",
            "Seminario: AI Safety",
        ]
        assert app.check_action("new_rule", ()) is True
        app.set_focus(None)
        app.query_one(TabbedContent).active = "courses"
        await pilot.pause()
        assert app.check_action("new_rule", ()) is False

    drive(app, scenario)


def test_new_rule_from_an_unmatched_event(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": [*SOURCE, SEMINAR]}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        events = await open_rules(app, pilot, "events")
        events.move_cursor(row=events.row_count - 1)
        await pilot.press("enter")
        await pilot.pause()
        form = editor(app)
        assert form.query_one("#rule-value", Input).value == "Seminario: "
        assert "Matches 1 of 6 events." in status_of(form)
        form.query_one("#rule-kind", Select).value = EventKind.DEADLINE
        form.query_one("#rule-title", Input).value = "🎤 {name}"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.screen is app.screen_stack[0]
        assert row_texts(app, "rules")[-1] == [
            "7",
            "Deadline",
            "title starts with 'Seminario: '",
            "🎤 {name}",
        ]
        assert "0 unmatched" in text_of(app, "#rules-summary")
        assert app.draft is not None and app.draft.is_dirty
        assert cells(app, "deadlines", "AI Safety")[0] == "AI Safety"

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert saved(config, "rules")[-1] == {
            "kind": "deadline",
            "field": "title",
            "match": "starts_with",
            "value": "Seminario: ",
            "ignore_case": False,
            "title": "🎤 {name}",
        }

        app.action_preview()
        await settle(app, pilot)
        summaries = {m.summary for m in app.plan.mutations} if app.plan else set()
        assert "🎤 AI Safety" in summaries

    drive(app, scenario)


def test_edit_a_rule_and_discard_the_preview(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        app.action_preview()
        await settle(app, pilot)
        assert app.plan is not None

        await open_rules(app, pilot)
        await pilot.press("e")
        await pilot.pause()
        form = editor(app)
        assert form.query_one("#rule-value", Input).value == "Esame: "
        form.query_one("#rule-title", Input).value = "📝 {name}"
        await pilot.pause()
        await pilot.click("#save-rule")
        await pilot.pause()

        assert row_texts(app, "rules")[0][3] == "📝 {name}"
        assert app.plan is None
        assert "Preferences changed" in summary_text(app)

    drive(app, scenario)


def test_invalid_rule_cannot_be_saved(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await open_rules(app, pilot)
        await pilot.press("n")
        await pilot.pause()
        form = editor(app)
        assert "the value to match is empty" in status_of(form)
        assert form.query_one("#save-rule", Button).disabled
        form.query_one("#rule-match", Select).value = MatchKind.REGEX
        form.query_one("#rule-value", Input).value = "(unclosed"
        await pilot.pause()
        assert "invalid regular expression" in status_of(form)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is form
        form.query_one("#rule-ignore-case", Checkbox).value = True
        await pilot.pause()
        await pilot.click("#cancel-rule")
        await pilot.pause()

        assert app.screen is app.screen_stack[0]
        assert len(row_texts(app, "rules")) == 6
        assert app.draft is not None and not app.draft.is_dirty

    drive(app, scenario)


def test_delete_and_move_rules(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        rules = await open_rules(app, pilot)
        rules.move_cursor(row=2)  # lectures by title
        await pilot.press("d")
        await pilot.pause()
        assert len(row_texts(app, "rules")) == 5
        assert app.draft is not None and app.draft.session.catalog.courses == ()
        assert rules.cursor_row == 2

        rules.move_cursor(row=0)
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert [row[2] for row in row_texts(app, "rules")[:2]] == [
            "a category is 'Esame'",
            "title starts with 'Esame: '",
        ]
        assert rules.cursor_row == 1
        await pilot.press("left_square_bracket", "left_square_bracket")
        await pilot.pause()
        assert row_texts(app, "rules")[0][2] == "title starts with 'Esame: '"
        assert rules.cursor_row == 0

    drive(app, scenario)


def test_edit_and_clear_the_exam_enrollment(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert cells(app, "exams", "CS (2027-01-20)")[2] == "enrolled"
        await open_rules(app, pilot, "enrollment")
        await pilot.press("enter")
        await pilot.pause()
        form = editor(app)
        assert not form.query("#rule-kind")
        assert not form.query("#rule-title")
        form.query_one("#rule-value", Input).value = "Registered"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert row_texts(app, "enrollment")[0][1] == (
            "description starts with 'Registered'"
        )
        assert cells(app, "exams", "CS (2027-01-20)")[2] == ""

        enrollment = app.query_one("#enrollment-table", DataTable)
        enrollment.focus()
        enrollment.move_cursor(row=1)
        await pilot.press("d")
        await pilot.pause()
        assert row_texts(app, "enrollment")[1][1] == "— not detected"
        # Moving only applies to the rules.
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert row_texts(app, "rules")[0][2] == "title starts with 'Esame: '"

    drive(app, scenario)


def test_rules_keys_on_the_events_table(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await open_rules(app, pilot, "events")
        await pilot.press("d")
        await pilot.pause()
        assert len(row_texts(app, "rules")) == 6
        await pilot.press("e")
        await pilot.pause()
        assert editor(app).query_one("#rule-value", Input).value == (
            "Lezione: Didattica - "
        )
        await pilot.press("escape")
        await pilot.pause()
        assert len(row_texts(app, "rules")) == 6

    drive(app, scenario)


def test_rules_actions_need_a_session(config: Config) -> None:
    app = make_app(config, FakeCalendarGateway())

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        assert app.draft is None
        app.action_new_rule()
        app.action_edit_rule()
        app.action_delete_rule()
        app.action_move_rule(1)
        app._rule_saved(None, None)
        app._enrollment_saved("enrolled", None)
        await pilot.pause()
        assert len(app.screen_stack) == 1

    drive(app, scenario)


def test_change_the_time_zone_of_new_calendars(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        app.action_preview()
        await settle(app, pilot)
        app.query_one(TabbedContent).active = "setup"
        await pilot.pause()
        assert "primary Google calendar" in text_of(app, "#setup-time-zone")

        await pilot.click("#change-time-zone")
        await pilot.pause()
        prompt = app.screen
        assert isinstance(prompt, TextPrompt)
        field = prompt.query_one(Input)
        field.value = "Rome"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is prompt
        assert "Unknown time zone 'Rome'" in str(
            prompt.query_one("#prompt-error", Label).render()
        )
        field.value = "Asia/Tokyo"
        await pilot.press("enter")
        await pilot.pause()

        assert app.calendars.time_zone == "Asia/Tokyo"
        assert text_of(app, "#setup-time-zone").startswith("Asia/Tokyo")
        assert saved(config, "calendars")["time_zone"] == "Asia/Tokyo"
        assert app.plan is not None  # only matters when the calendar is created

        app.action_apply()
        await settle(app, pilot)
        assert gateway.time_zones == {"Tgt": "Asia/Tokyo"}

        # Empty: back to the primary calendar's time zone; escape cancels.
        app.query_one(TabbedContent).active = "setup"  # apply showed the Sync tab
        await pilot.pause()
        await pilot.click("#change-time-zone")
        await pilot.pause()
        assert isinstance(app.screen, TextPrompt)
        await pilot.press("escape")
        await pilot.pause()
        assert app.calendars.time_zone == "Asia/Tokyo"
        await pilot.click("#change-time-zone")
        await pilot.pause()
        assert isinstance(app.screen, TextPrompt)
        app.screen.query_one(Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert app.calendars.time_zone is None
        assert saved(config, "calendars")["time_zone"] is None

    drive(app, scenario)


# -- first run ----------------------------------------------------------------


def drive_wizard(app: UnicalApp, scenario: Scenario) -> None:
    """Like ``drive``, without waiting for the workers: the guide waits for us."""

    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(main())


async def until(
    pilot: Pilot[None], condition: Callable[[], object], timeout: float = 5
) -> None:
    for _ in range(int(timeout / 0.02)):
        if condition():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not reached")


async def screen_of(app: UnicalApp, pilot: Pilot[None], kind: type) -> Any:
    await until(pilot, lambda: isinstance(app.screen, kind))
    return app.screen


def first_run_app(
    config: Config, gateway: FakeCalendarGateway, **kwargs: Any
) -> UnicalApp:
    return make_app(
        config, gateway, setup=make_setup(gateway, first_run=True, **kwargs)
    )


def test_first_run_guide_keeping_the_rules(config: Config) -> None:
    gateway = FakeCalendarGateway({"Me": [], "Uni": SOURCE})
    gateway.read_only = {"Uni"}
    app = first_run_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")

        source = await screen_of(app, pilot, CalendarPicker)
        assert source.title_text.startswith("Step 1")
        source.query_one(OptionList).highlighted = 1
        await pilot.press("enter")

        target = await screen_of(app, pilot, CalendarPicker)
        assert target is not source and target.title_text.startswith("Step 2")
        assert app.calendars.source == "Uni"
        assert saved(config, "calendars")["source"] == "Uni"
        assert [(c.name, c.allowed) for c in target.choices] == [
            ("Me", True),
            ("Uni", False),
        ]
        field = target.query_one(Input)
        assert field.value == "Uni Colored"
        field.focus()
        await pilot.press("enter")

        rules = await screen_of(app, pilot, WizardRules)
        assert app.calendars.target == "Uni Colored"
        assert "2 lectures · 2 exams · 1 deadlines · 0 unmatched" in str(
            rules.query_one(".wizard-text", Static).render()
        )
        await pilot.press("enter")

        await until(pilot, lambda: app.plan is not None)
        assert app.query_one(TabbedContent).active == "sync"
        assert saved(config, "calendars")["target"] == "Uni Colored"
        assert gateway.created == []  # nothing written before applying
        assert gateway.batches == []

    drive_wizard(app, scenario)


@pytest.mark.parametrize(("button", "rules"), [("#rules-edit", 6), ("#rules-empty", 0)])
def test_first_run_guide_adjusting_the_rules(
    config: Config, button: str, rules: int
) -> None:
    gateway = FakeCalendarGateway({"Src": [*SOURCE, SEMINAR]})
    app = first_run_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")
        await screen_of(app, pilot, CalendarPicker)
        await pilot.press("enter")  # the current source
        target = await screen_of(app, pilot, CalendarPicker)
        target.query_one(OptionList).highlighted = None
        target.query_one(Input).value = "Tgt"
        target.query_one(Input).focus()
        await pilot.press("enter")

        wizard = await screen_of(app, pilot, WizardRules)
        assert "1 event(s) match no rule" in str(
            wizard.query_one(".wizard-text", Static).render()
        )
        assert wizard.query_one(DataTable).get_row_at(0)[0].plain == "unmatched"
        await pilot.click(button)

        await until(pilot, lambda: app.query_one(TabbedContent).active == "rules")
        assert app.draft is not None
        assert len(app.draft.session.profile.rules) == rules
        assert app.draft.is_dirty is (rules == 0)
        assert app.plan is None

    drive_wizard(app, scenario)


def test_first_run_guide_can_be_skipped(config: Config) -> None:
    app = first_run_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("escape")
        await until(pilot, lambda: app.draft is not None)
        assert len(app.screen_stack) == 1
        assert not config.profile_path.exists()

    drive_wizard(app, scenario)


def test_leaving_the_source_step_loads_the_current_source(config: Config) -> None:
    app = first_run_app(config, FakeCalendarGateway({"Other": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")
        await screen_of(app, pilot, CalendarPicker)
        await pilot.press("escape")
        # "Src" doesn't exist: the guide ends on the Setup tab.
        await until(pilot, lambda: app.query_one(TabbedContent).active == "setup")
        await until(pilot, lambda: not app.busy)
        assert app.draft is None
        assert len(app.screen_stack) == 1

    drive_wizard(app, scenario)


def test_leaving_the_target_step_ends_the_guide(config: Config) -> None:
    app = first_run_app(config, FakeCalendarGateway({"Src": SOURCE}))

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")
        await screen_of(app, pilot, CalendarPicker)
        await pilot.press("enter")
        target = await screen_of(app, pilot, CalendarPicker)
        await until(pilot, lambda: app.draft is not None)
        await pilot.press("escape")
        await until(pilot, lambda: app.screen is not target)
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.calendars.target == "Tgt"
        assert app.plan is None

    drive_wizard(app, scenario)


def test_first_run_guide_with_an_ical_source(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    app = first_run_app(config, gateway, fixed_source="iCal feed at x")

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")
        picker = await screen_of(app, pilot, CalendarPicker)
        assert picker.title_text.startswith("Step 2")
        await pilot.press("escape")
        await until(pilot, lambda: len(app.screen_stack) == 1)
        assert app.draft is not None

    drive_wizard(app, scenario)


def test_first_run_guide_without_the_calendar_list(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})

    def boom() -> None:
        raise RuntimeError("offline")

    gateway.list_calendars = boom  # type: ignore[assignment,method-assign]
    app = first_run_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await screen_of(app, pilot, WizardIntro)
        await pilot.press("enter")
        await until(pilot, lambda: app.draft is not None and not app.busy)
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert "Could not list your calendars: offline" in (await log_text(app, pilot))

    drive_wizard(app, scenario)

from datetime import date
from unical.tui.app import _parse_window_string, _validate_window_input


def test_parse_and_validate_window_string() -> None:
    assert _parse_window_string("all") == (None, None, True)
    assert _parse_window_string("all-time") == (None, None, True)

    w_from, w_to, all_t = _parse_window_string("semester")
    assert not all_t
    assert w_from is not None and w_to is not None

    assert _parse_window_string("2026-03-01..2026-09-15") == (
        date(2026, 3, 1),
        date(2026, 9, 15),
        False,
    )
    assert _parse_window_string("2026-03-01 to 2026-09-15") == (
        date(2026, 3, 1),
        date(2026, 9, 15),
        False,
    )

    assert _validate_window_input("all") is None
    assert _validate_window_input("2026-03-01..2026-09-15") is None
    assert _validate_window_input("invalid date") is not None


def test_tui_window_and_course_updates(config: Config) -> None:
    gateway = FakeCalendarGateway({"Src": SOURCE})
    save_profile(config, calendars={"source": "Src", "target": "Tgt"})
    app = make_app(config, gateway)

    async def scenario(app: UnicalApp, pilot: Pilot[None]) -> None:
        await settle(app, pilot)
        assert app.draft is not None

        # Update window
        app._window_chosen("2026-03-01..2026-09-15")
        await settle(app, pilot)
        assert app.options.window_from == date(2026, 3, 1)
        assert app.options.window_to == date(2026, 9, 15)
        assert not app.options.all_time
        assert "2026-03-01 → 2026-09-15" in str(app.query_one("#setup-window", Static).render())

        # Update course
        app._course_chosen("CS")
        await settle(app, pilot)
        assert app.options.course == "CS"
        assert "'CS'" in str(app.query_one("#setup-course", Static).render())

        # Check sync tab reminder
        reminder = str(app.query_one("#sync-scope-reminder", Static).render())
        assert "2026-03-01 → 2026-09-15" in reminder
        assert "'CS'" in reminder

    drive(app, scenario)
