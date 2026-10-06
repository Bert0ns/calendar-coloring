"""Textual application: edit preferences, preview the changes, apply them.

The app only talks to the :class:`SyncWorkflow` phases and the
:class:`PreferencesDraft` view model: no Calendar API calls, no file I/O.
Slow phases run in worker threads; the reporter posts back thread-safely.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import ClassVar, Literal

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from polimi_calendar_coloring.palette import GoogleColor
from polimi_calendar_coloring.sync.models import SyncPlan, SyncResult
from polimi_calendar_coloring.sync.source import EventSource
from polimi_calendar_coloring.tui.model import (
    ColorRow,
    ExamRow,
    ItemStatus,
    PreferencesDraft,
)
from polimi_calendar_coloring.tui.widgets import (
    ColorPicker,
    PlanTree,
    plan_summary,
    swatch,
)
from polimi_calendar_coloring.workflow import SyncOptions, SyncWorkflow

Level = Literal["detail", "info", "warning", "error"]

COURSES, EXAMS, DEADLINES, SYNC = "courses", "exams", "deadlines", "sync"
EDITABLE_TABS = (COURSES, EXAMS, DEADLINES)

_LEVEL_STYLES: dict[Level, str] = {
    "detail": "dim",
    "info": "",
    "warning": "yellow",
    "error": "bold red",
}

_STATUS_STYLES = {
    ItemStatus.SAVED: ("saved", "green"),
    ItemStatus.MODIFIED: ("● modified", "bold yellow"),
    ItemStatus.SUGGESTED: ("suggested", "italic dim"),
    ItemStatus.UNSET: ("not set", "dim"),
}


class ReporterMessage(Message):
    def __init__(self, level: Level, text: str) -> None:
        super().__init__()
        self.level = level
        self.text = text


class ApplyProgress(Message):
    def __init__(self, completed: int, total: int) -> None:
        super().__init__()
        self.completed = completed
        self.total = total


class TuiReporter:
    """:class:`Reporter` that forwards everything to the app as messages, so it
    can be used from worker threads."""

    def __init__(self) -> None:
        self._app: App[None] | None = None

    def connect(self, app: App[None]) -> None:
        self._app = app

    def _post(self, level: Level, text: str) -> None:
        if self._app is not None:
            self._app.post_message(ReporterMessage(level, text))

    def detail(self, message: str) -> None:
        self._post("detail", message)

    def info(self, message: str) -> None:
        self._post("info", message)

    def warning(self, message: str) -> None:
        self._post("warning", message)

    def error(self, message: str) -> None:
        self._post("error", message)

    def sync_started(self, source_label: str, target_name: str) -> None:
        self.info(f"Loading events from {source_label}...")

    def target_calendar_missing(self, target_name: str, dry_run: bool) -> None:
        self.info(f"Target calendar '{target_name}' not found: applying creates it.")

    def plan_ready(self, plan: SyncPlan) -> None:
        self.info(
            f"Compared {plan.source_event_count} source and "
            f"{plan.target_event_count} target events."
        )

    def applying(self, plan: SyncPlan) -> None:
        self.info(f"Executing {len(plan.mutations)} calendar operation(s)...")

    def sync_finished(self, result: SyncResult) -> None:
        for failure in result.failures:
            mutation = failure.mutation
            self.error(
                f"Failed to {mutation.action.value} '{mutation.summary}': "
                f"{failure.error}"
            )
        self.info(_result_headline(result).plain)

    def dry_run_finished(self, plan: SyncPlan) -> None:
        pass


def _result_headline(result: SyncResult) -> Text:
    counts = (
        f"{result.inserted} inserted, {result.updated} updated, "
        f"{result.deleted} deleted"
    )
    if result.succeeded:
        return Text(f"✔ Sync finished: {counts}.", style="bold green")
    return Text(
        f"✖ Sync finished with {len(result.failures)} failure(s): {counts}.",
        style="bold red",
    )


def _status(status: ItemStatus) -> Text:
    label, style = _STATUS_STYLES[status]
    return Text(label, style=style)


def _subscribed(subscribed: bool | None) -> Text:
    if subscribed is None:
        return Text("?", style="dim")
    return Text("✔ yes", style="green") if subscribed else Text("✘ no", style="dim")


class PolimiCalendarApp(App[None]):
    TITLE = "Polimi Calendar Coloring"

    CSS = """
    .help {
        color: $text-muted;
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    #sync-actions {
        height: auto;
    }
    #sync-actions Button {
        margin-right: 2;
    }
    #plan-summary {
        height: auto;
        padding: 1 1;
    }
    #plan-tree {
        height: 1fr;
        border: round $panel;
    }
    #progress {
        padding: 0 1;
    }
    #log {
        height: 8;
        border: round $panel;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("c", "pick_color", "Color"),
        Binding("space", "toggle_subscription", "Subscribed"),
        Binding("ctrl+s", "save", "Save"),
        Binding("p", "preview", "Preview"),
        Binding("a", "apply", "Apply"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        workflow: SyncWorkflow,
        source: EventSource,
        options: SyncOptions,
        target_name: str,
        reporter: TuiReporter,
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.source = source
        self.options = options
        self.target_name = target_name
        self.draft: PreferencesDraft | None = None
        self.plan: SyncPlan | None = None
        self.busy = False
        self._quit_requested = False
        self._base_sub_title = f"{source.label} ➔ '{target_name}'"
        self.sub_title = self._base_sub_title
        reporter.connect(self)

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        target = self.options.target
        yield Header()
        with TabbedContent(id="tabs"):
            if target.includes_lectures:
                with TabPane("Courses", id=COURSES):
                    yield Static(
                        "Every lecture of a course gets the course color. "
                        "Enter/c: pick a color.",
                        classes="help",
                    )
                    yield self._table(COURSES, "Course", "Color", "Status")
            if target.includes_exams:
                with TabPane("Exams", id=EXAMS):
                    yield Static(
                        "Space: toggle subscription · Enter/c: pick a color.",
                        classes="help",
                    )
                    yield self._table(
                        EXAMS,
                        "Exam",
                        "Date",
                        "Polimi says",
                        "Subscribed",
                        "Color",
                        "Status",
                    )
            if target.includes_deadlines:
                with TabPane("Deadlines", id=DEADLINES):
                    yield Static("Enter/c: pick a color.", classes="help")
                    yield self._table(DEADLINES, "Deadline", "Color", "Status")
            with TabPane("Sync", id=SYNC):
                with Horizontal(id="sync-actions"):
                    yield Button("Preview changes", id="preview", variant="primary")
                    yield Button("Apply", id="apply", variant="success")
                yield Static(
                    "Preview the changes, then apply them to Google Calendar.",
                    id="plan-summary",
                )
                yield PlanTree(id="plan-tree")
                yield ProgressBar(id="progress", show_eta=False)
                yield RichLog(id="log", wrap=True)
        yield Footer()

    @staticmethod
    def _table(tab: str, *columns: str) -> DataTable[Text]:
        table: DataTable[Text] = DataTable(
            id=f"{tab}-table", cursor_type="row", zebra_stripes=True
        )
        table.add_columns(*columns)
        return table

    def on_mount(self) -> None:
        self._ui.query_one("#progress").display = False
        for table in self._ui.query(DataTable):
            table.loading = True
        self.load_session()

    # -- state ---------------------------------------------------------------

    @property
    def _ui(self) -> Screen[object]:
        """The main screen, also while a dialog is shown on top of it."""
        return self.screen_stack[0]

    def _active_tab(self) -> str:
        return self._ui.query_one(TabbedContent).active

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._ui.query_one("#preview", Button).disabled = busy or self.draft is None
        self._ui.query_one("#apply", Button).disabled = busy or self.plan is None
        self.refresh_bindings()

    def _set_plan(self, plan: SyncPlan | None) -> None:
        self.plan = plan
        self._set_busy(self.busy)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        tab = self._active_tab()
        if action == "pick_color":
            return self.draft is not None and tab in EDITABLE_TABS
        if action == "toggle_subscription":
            return self.draft is not None and tab == EXAMS
        if action == "save":
            return self.draft is not None
        if action == "preview":
            return None if self.busy or self.draft is None else True
        if action == "apply":
            return None if self.busy or self.plan is None else True
        return True

    def on_tabbed_content_tab_activated(self) -> None:
        self.refresh_bindings()

    # -- logging -------------------------------------------------------------

    def on_reporter_message(self, message: ReporterMessage) -> None:
        self._write_log(message.level, message.text)

    def _write_log(self, level: Level, text: str) -> None:
        self._ui.query_one(RichLog).write(Text(text, style=_LEVEL_STYLES[level]))
        if level == "warning" or level == "error":
            self.notify(text, severity=level)

    # -- tables --------------------------------------------------------------

    def _refresh_tables(self) -> None:
        draft = self.draft
        if draft is None:
            return
        if self.options.target.includes_lectures:
            self._fill(COURSES, [self._color_cells(r) for r in draft.course_rows()])
        if self.options.target.includes_exams:
            self._fill(EXAMS, [self._exam_cells(r) for r in draft.exam_rows()])
        if self.options.target.includes_deadlines:
            self._fill(DEADLINES, [self._color_cells(r) for r in draft.deadline_rows()])
        dirty = " • unsaved changes" if draft.is_dirty else ""
        self.sub_title = self._base_sub_title + dirty

    def _fill(self, tab: str, rows: list[tuple[str, list[Text]]]) -> None:
        table = self._ui.query_one(f"#{tab}-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        for key, cells in rows:
            table.add_row(*cells, key=key)
        if rows:
            table.move_cursor(row=min(cursor, len(rows) - 1))

    @staticmethod
    def _color_cells(row: ColorRow) -> tuple[str, list[Text]]:
        return row.name, [Text(row.name), swatch(row.color), _status(row.status)]

    @staticmethod
    def _exam_cells(row: ExamRow) -> tuple[str, list[Text]]:
        return row.key, [
            Text(row.exam.title),
            Text(row.exam.date),
            Text(row.hint, style="italic"),
            _subscribed(row.subscribed),
            swatch(row.color),
            _status(row.status),
        ]

    def _cursor_key(self, tab: str) -> str | None:
        table = self._ui.query_one(f"#{tab}-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return row_key.value

    def _preferences_changed(self) -> None:
        self._quit_requested = False
        if self.plan is not None:
            self._set_plan(None)
            self._ui.query_one("#plan-summary", Static).update(
                "Preferences changed: preview the changes again."
            )
            self._ui.query_one(PlanTree).clear()
        self._refresh_tables()

    # -- editing -------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        tab = (event.data_table.id or "").removesuffix("-table")
        if event.row_key.value is not None:
            self._pick_color(tab, event.row_key.value)

    def action_pick_color(self) -> None:
        tab = self._active_tab()
        key = self._cursor_key(tab)
        if key is not None:
            self._pick_color(tab, key)

    def _pick_color(self, tab: str, key: str) -> None:
        draft = self.draft
        if draft is None:
            return
        current: GoogleColor | None
        if tab == EXAMS:
            row = draft.exam_row(key)
            current, subject = row.color, f"{row.exam.title} ({row.exam.date})"
        else:
            rows = draft.course_rows() if tab == COURSES else draft.deadline_rows()
            current = next(r.color for r in rows if r.name == key)
            subject = key
        self.push_screen(
            ColorPicker(subject, current), partial(self._color_chosen, tab, key)
        )

    def _color_chosen(self, tab: str, key: str, color: GoogleColor | None) -> None:
        if color is None or self.draft is None:
            return
        if tab == COURSES:
            self.draft.set_course_color(key, color)
        elif tab == DEADLINES:
            self.draft.set_deadline_color(key, color)
        else:
            self.draft.set_exam_color(key, color)
        self._preferences_changed()

    def action_toggle_subscription(self) -> None:
        key = self._cursor_key(EXAMS)
        if self.draft is None or key is None:
            return
        self.draft.toggle_exam_subscription(key)
        self._preferences_changed()

    def action_save(self) -> None:
        draft = self.draft
        if draft is None:
            return
        if not draft.is_dirty:
            self.notify("No changes to save.")
            return
        try:
            self.workflow.save_preferences(draft.session)
        except OSError as exc:
            self._write_log("error", f"Could not save preferences: {exc}")
            return
        draft.mark_saved()
        self._refresh_tables()
        self.notify("Preferences saved.")

    async def action_quit(self) -> None:
        if self.draft is not None and self.draft.is_dirty and not self._quit_requested:
            self._quit_requested = True
            self.notify(
                "Unsaved changes: press q again to quit without saving, "
                "or ctrl+s to save.",
                severity="warning",
            )
            return
        self.exit()

    # -- phases (run in worker threads) --------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview":
            self.action_preview()
        elif event.button.id == "apply":
            self.action_apply()

    def action_preview(self) -> None:
        if not self.busy and self.draft is not None:
            self._ui.query_one(TabbedContent).active = SYNC
            self.preview_changes()

    def action_apply(self) -> None:
        if not self.busy and self.plan is not None:
            self._ui.query_one(TabbedContent).active = SYNC
            self.apply_changes()

    @work(exclusive=True, group="calendar")
    async def load_session(self) -> None:
        self._set_busy(True)
        try:
            session = await asyncio.to_thread(
                self.workflow.load, self.options, self.source, self.target_name
            )
        except Exception as exc:
            self._write_log("error", f"Could not load the source events: {exc}")
        else:
            self.draft = PreferencesDraft(session)
            self._refresh_tables()
        for table in self._ui.query(DataTable):
            table.loading = False
        self._set_busy(False)
        if self.draft is not None and self._active_tab() in EDITABLE_TABS:
            self._ui.query_one(f"#{self._active_tab()}-table").focus()

    @work(exclusive=True, group="calendar")
    async def preview_changes(self) -> None:
        assert self.draft is not None
        self._set_busy(True)
        try:
            plan = await asyncio.to_thread(self.workflow.preview, self.draft.session)
        except Exception as exc:
            self._write_log("error", f"Could not compute the changes: {exc}")
            return
        finally:
            self._set_busy(False)
        self._set_plan(plan)
        self._ui.query_one("#plan-summary", Static).update(plan_summary(plan))
        self._ui.query_one(PlanTree).show_plan(plan)

    @work(exclusive=True, group="calendar")
    async def apply_changes(self) -> None:
        assert self.draft is not None and self.plan is not None
        draft, plan = self.draft, self.plan
        self._set_busy(True)
        progress = self._ui.query_one(ProgressBar)
        progress.update(total=len(plan.mutations) or 1, progress=0)
        progress.display = True
        try:
            # Same as a CLI sync: what the user never chose is filled in by the
            # automatic rules (the preview already showed it) and saved.
            self.workflow.complete_preferences(draft.session)
            self.workflow.save_preferences(draft.session)
            draft.mark_saved()
            result = await asyncio.to_thread(
                self.workflow.apply, draft.session, plan, self._report_progress
            )
        except Exception as exc:
            self._write_log("error", f"Could not apply the changes: {exc}")
            return
        finally:
            progress.display = False
            self._set_busy(False)
            self._refresh_tables()
        self._set_plan(None)
        self._ui.query_one("#plan-summary", Static).update(_result_headline(result))
        self._ui.query_one(PlanTree).clear()

    def _report_progress(self, completed: int, total: int) -> None:
        self.post_message(ApplyProgress(completed, total))

    def on_apply_progress(self, message: ApplyProgress) -> None:
        self._ui.query_one(ProgressBar).update(
            total=message.total, progress=message.completed
        )
