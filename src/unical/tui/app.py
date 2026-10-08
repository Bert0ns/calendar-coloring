"""Textual application: choose the calendars, edit the rules and preferences,
preview the changes, apply them.

The app only talks to the :class:`SyncWorkflow` phases and the
:class:`PreferencesDraft` view model: no Calendar API calls, no file I/O.
Slow phases run in worker threads; the reporter posts back thread-safely.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import date
from functools import partial
from typing import Any, ClassVar, Literal

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from unical.events import Event
from unical.palette import GoogleColor
from unical.profile import Profile, time_zone_error
from unical.rules import Condition, Rule
from unical.semesters import current_semester_window
from unical.sync.models import CalendarInfo, SyncPlan, SyncResult
from unical.sync.source import SourceCalendarNotFoundError
from unical.targets import SyncTarget
from unical.tui.model import (
    ColorRow,
    ExamRow,
    ItemStatus,
    PreferencesDraft,
)
from unical.tui.rules import (
    RuleForm,
    TitlePreview,
    count_matches,
    describe,
    preview,
    suggest_prefix,
    summary,
)
from unical.tui.setup import (
    CalendarSetup,
    Role,
    calendar_choices,
    target_name_error,
)
from unical.tui.widgets import (
    KIND_LABELS,
    CalendarPicker,
    ColorPicker,
    PlanTree,
    RuleEditor,
    SyncProgress,
    TextPrompt,
    plan_summary,
    swatch,
)
from unical.tui.wizard import RulesChoice, WizardIntro, WizardRules
from unical.version_check import check_for_updates, should_check_for_updates
from unical.workflow import SyncOptions, SyncWorkflow

SCOPE_LABELS = {
    SyncTarget.ALL: "Everything",
    SyncTarget.LECTURES: "Lectures only",
    SyncTarget.EXAMS: "Exams only",
    SyncTarget.DEADLINES: "Deadlines only",
}
"""What the Sync tab can limit a sync to."""

Level = Literal["detail", "info", "warning", "error"]

COURSES, EXAMS, DEADLINES, SYNC, RULES, SETUP = (
    "courses",
    "exams",
    "deadlines",
    "sync",
    "rules",
    "setup",
)
ENROLLMENT, EVENTS = "enrollment", "events"
ENROLLMENT_ROWS = {"enrolled": "Enrolled", "not_enrolled": "Not enrolled"}
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

    def update_available(self, current: str, latest: str) -> None:
        self.info(f"Update available: {current} ➔ {latest}")


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


def _time_zone_label(time_zone: str | None) -> str:
    return time_zone or "the time zone of your primary Google calendar"


def _optional_time_zone_error(value: str) -> str | None:
    return time_zone_error(value) if value.strip() else None


def _parse_window_string(val: str) -> tuple[date | None, date | None, bool]:
    s = val.strip().lower()
    if s in ("all", "all-time", "all time"):
        return None, None, True
    if s in ("semester", "current", "current semester", "sem"):
        d_from, d_to, _ = current_semester_window()
        return d_from, d_to, False
    parts = [
        p.strip()
        for p in s.replace("to", "..")
        .replace("→", "..")
        .replace("->", "..")
        .split("..")
    ]
    if len(parts) == 1:
        d = date.fromisoformat(parts[0])
        return d, None, False
    if len(parts) == 2:
        d_from_parsed: date | None = date.fromisoformat(parts[0]) if parts[0] else None
        d_to_parsed: date | None = date.fromisoformat(parts[1]) if parts[1] else None
        return d_from_parsed, d_to_parsed, False
    raise ValueError(
        "Invalid format. Use 'all', 'semester', or 'YYYY-MM-DD..YYYY-MM-DD'."
    )


def _validate_window_input(value: str) -> str | None:
    try:
        d_from, d_to, _ = _parse_window_string(value)
        if d_from is not None and d_to is not None and d_from > d_to:
            return "Start date cannot be after end date."
        return None
    except Exception as exc:
        return f"Invalid window ({exc}). Use 'all', 'semester', or 'YYYY-MM-DD..YYYY-MM-DD'."


def _condition(condition: Condition | None) -> Text:
    if condition is None:
        return Text("— not detected", style="dim")
    return Text(describe(condition))


def _preview_cells(item: TitlePreview) -> list[Text]:
    kind = (
        Text(KIND_LABELS[item.kind])
        if item.kind is not None
        else Text("unmatched", style="yellow")
    )
    # Unmatched events have no name: their title stands in, dimmed.
    name = Text(item.name) if item.kind is not None else Text(item.title, style="dim")
    return [kind, Text(str(item.count), style="dim"), name, Text(item.title)]


def _subscribed(subscribed: bool | None) -> Text:
    if subscribed is None:
        return Text("?", style="dim")
    return Text("✔ yes", style="green") if subscribed else Text("✘ no", style="dim")


class UnicalApp(App[None]):
    TITLE = "Uni Calendar Coloring"

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
    .scope {
        width: 24;
    }
    #sync-actions .scope {
        margin-right: 2;
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
        height: auto;
        padding: 0 1;
    }
    #log {
        height: 8;
        border: round $panel;
    }
    .setup-row {
        height: auto;
        margin: 1 0 0 0;
    }
    .setup-label {
        width: 10;
        padding: 1 1;
        text-style: bold;
    }
    .setup-value {
        width: 1fr;
        padding: 1 1;
    }
    #setup-notes {
        margin-top: 1;
    }
    #rules-panes {
        height: 1fr;
    }
    #rules-left {
        width: 2fr;
    }
    #rules-right {
        width: 3fr;
    }
    #enrollment-table {
        height: 4;
        margin-top: 1;
    }
    #rules-summary {
        height: auto;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("c", "pick_color", "Color"),
        Binding("space", "toggle_subscription", "Subscribed"),
        Binding("ctrl+s", "save", "Save"),
        Binding("p", "preview", "Preview"),
        Binding("a", "apply", "Apply"),
        Binding("n", "new_rule", "New rule"),
        Binding("e", "edit_rule", "Edit"),
        Binding("d", "delete_rule", "Delete"),
        Binding("left_square_bracket", "move_rule(-1)", "Up"),
        Binding("right_square_bracket", "move_rule(1)", "Down"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        workflow: SyncWorkflow,
        setup: CalendarSetup,
        options: SyncOptions,
        reporter: TuiReporter,
        check_update: bool = True,
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.setup = setup
        self.calendars = setup.calendars
        self.source = setup.source_for(setup.calendars.source)
        self.options = options
        self.profile: Profile | None = None
        self.draft: PreferencesDraft | None = None
        self.plan: SyncPlan | None = None
        self.busy = False
        self._calendar_list: list[CalendarInfo] | None = None
        self._events: list[Event] = []
        """The syncable source events, as shown in the Rules tab."""
        self._title_previews: list[TitlePreview] = []
        self._quit_requested = False
        self.sub_title = self._base_sub_title
        self._check_update = check_update
        reporter.connect(self)

    @property
    def _base_sub_title(self) -> str:
        return f"{self.source.label} ➔ '{self.calendars.target}'"

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs", initial=SETUP):
            with TabPane("Setup", id=SETUP):
                yield Static(
                    "The calendar to read your timetable from, and the one to "
                    "write the colored copy to. Choices are saved right away.",
                    classes="help",
                )
                with Horizontal(classes="setup-row"):
                    yield Static("Source", classes="setup-label")
                    yield Static(id="setup-source", classes="setup-value")
                    yield Button("Change…", id="change-source")
                with Horizontal(classes="setup-row"):
                    yield Static("Target", classes="setup-label")
                    yield Static(id="setup-target", classes="setup-value")
                    yield Button("Change…", id="change-target")
                with Horizontal(classes="setup-row"):
                    yield Static("Time zone", classes="setup-label")
                    yield Static(id="setup-time-zone", classes="setup-value")
                    yield Button("Change…", id="change-time-zone")
                with Horizontal(classes="setup-row"):
                    yield Static("Window", classes="setup-label")
                    yield Static(id="setup-window", classes="setup-value")
                    yield Button("Change…", id="change-window")
                with Horizontal(classes="setup-row"):
                    yield Static("Course", classes="setup-label")
                    yield Static(id="setup-course", classes="setup-value")
                    yield Button("Change…", id="change-course")
                with Horizontal(classes="setup-row"):
                    yield Static("Sync", classes="setup-label")
                    yield self._scope_select("scope-setup")
                    yield Static(
                        "What a sync covers; the rest is left untouched.",
                        classes="setup-value",
                    )
                yield Static(id="setup-notes", classes="help")
            with TabPane("Courses", id=COURSES):
                yield Static(
                    "Every lecture of a course gets the course color. "
                    "Enter/c: pick a color.",
                    classes="help",
                )
                yield self._table(COURSES, "Course", "Color", "Status")
            with TabPane("Exams", id=EXAMS):
                yield Static(
                    "Space: toggle subscription · Enter/c: pick a color.",
                    classes="help",
                )
                yield self._table(
                    EXAMS,
                    "Exam",
                    "Date",
                    "Source says",
                    "Subscribed",
                    "Color",
                    "Status",
                )
            with TabPane("Deadlines", id=DEADLINES):
                yield Static("Enter/c: pick a color.", classes="help")
                yield self._table(DEADLINES, "Deadline", "Color", "Status")
            with TabPane("Sync", id=SYNC):
                yield Static(id="sync-scope-reminder", classes="help")
                with Horizontal(id="sync-actions"):
                    yield self._scope_select("scope-sync")
                    yield Button("Preview changes", id="preview", variant="primary")
                    yield Button("Apply", id="apply", variant="success")
                yield Static(
                    "Preview the changes, then apply them to Google Calendar.",
                    id="plan-summary",
                )
                yield PlanTree(id="plan-tree")
                yield SyncProgress(id="progress")
                yield RichLog(id="log", wrap=True)
            with TabPane("Rules", id=RULES):
                yield Static(
                    "The first rule matching an event decides what it is. "
                    "n: new rule · e/Enter: edit · d: delete · [ ]: move · "
                    "Enter on an event: new rule from its title.",
                    classes="help",
                )
                with Horizontal(id="rules-panes"):
                    with Vertical(id="rules-left"):
                        yield self._table(RULES, "#", "Kind", "Condition", "Title")
                        yield self._table(ENROLLMENT, "Exam enrollment", "Condition")
                    with Vertical(id="rules-right"):
                        yield Static(id="rules-summary")
                        yield self._table(EVENTS, "Kind", "×", "Name", "Event title")
        yield Footer()

    def _scope_select(self, select_id: str) -> Select[SyncTarget]:
        return Select(
            [(label, target) for target, label in SCOPE_LABELS.items()],
            value=self.options.target,
            allow_blank=False,
            id=select_id,
            classes="scope",
        )

    @staticmethod
    def _table(tab: str, *columns: str) -> DataTable[Text]:
        table: DataTable[Text] = DataTable(
            id=f"{tab}-table", cursor_type="row", zebra_stripes=True
        )
        table.add_columns(*columns)
        return table

    def on_mount(self) -> None:
        self._ui.query_one("#progress").display = False
        self._show_scope_tabs()
        self._refresh_setup()
        if self._check_update and should_check_for_updates():
            self._check_for_updates_worker()
        if self.setup.first_run:
            self.run_wizard()
        else:
            self.load_session()

    @work(thread=True)
    def _check_for_updates_worker(self) -> None:
        result = check_for_updates()
        if result.has_update and result.latest_version:
            self.call_from_thread(
                self.notify,
                f"A new version of unical ({result.latest_version}) is available!\n"
                "Run: pip install --upgrade uni-calendar-coloring",
                title="Update available",
                severity="information",
                timeout=10,
            )

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
        no_profile = busy or self.profile is None
        self._ui.query_one("#change-source", Button).disabled = (
            no_profile or self.setup.fixed_source is not None
        )
        self._ui.query_one("#change-target", Button).disabled = no_profile
        self._ui.query_one("#change-time-zone", Button).disabled = no_profile
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
        if action in ("new_rule", "edit_rule", "delete_rule", "move_rule"):
            return self.draft is not None and tab == RULES
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
        self._refresh_rules(draft)
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

    def _refresh_rules(self, draft: PreferencesDraft) -> None:
        profile = draft.session.profile
        self._fill(
            RULES,
            [
                (
                    str(index),
                    [
                        Text(str(index + 1), style="dim"),
                        Text(KIND_LABELS[rule.kind]),
                        Text(describe(rule.condition)),
                        Text(rule.title),
                    ],
                )
                for index, rule in enumerate(profile.rules)
            ],
        )
        self._fill(
            ENROLLMENT,
            [
                (key, [Text(label), _condition(getattr(profile.enrollment, key))])
                for key, label in ENROLLMENT_ROWS.items()
            ],
        )
        self._events = self.workflow.syncable_events(draft.session)
        self._title_previews = preview(self._events, profile.classifier)
        self._ui.query_one("#rules-summary", Static).update(
            summary(self._title_previews)
        )
        self._fill(
            EVENTS,
            [
                (str(index), _preview_cells(item))
                for index, item in enumerate(self._title_previews)
            ],
        )

    def _cursor_key(self, tab: str) -> str | None:
        table = self._ui.query_one(f"#{tab}-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return row_key.value

    def _preferences_changed(self) -> None:
        self._quit_requested = False
        self._discard_plan("Preferences changed: preview the changes again.")
        self._refresh_tables()

    def _discard_plan(self, reason: str) -> None:
        if self.plan is not None:
            self._set_plan(None)
            self._ui.query_one("#plan-summary", Static).update(reason)
            self._ui.query_one(PlanTree).clear()

    # -- editing -------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        tab = (event.data_table.id or "").removesuffix("-table")
        key = event.row_key.value
        if key is None:
            return
        if tab in (RULES, ENROLLMENT, EVENTS):
            self._edit_rules_row(tab, key)
        else:
            self._pick_color(tab, key)

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

    # -- rules ---------------------------------------------------------------

    def _rules_table(self) -> str:
        """The table of the Rules tab the keys act on (the rules by default)."""
        focused = self.focused.id if self.focused is not None else None
        for tab in (ENROLLMENT, EVENTS):
            if focused == f"{tab}-table":
                return tab
        return RULES

    def action_new_rule(self) -> None:
        self._open_rule_editor("New rule", RuleForm(), None)

    def action_edit_rule(self) -> None:
        tab = self._rules_table()
        key = self._cursor_key(tab)
        if key is not None:
            self._edit_rules_row(tab, key)

    def _edit_rules_row(self, tab: str, key: str) -> None:
        draft = self.draft
        if draft is None:
            return
        profile = draft.session.profile
        if tab == RULES:
            index = int(key)
            self._open_rule_editor(
                f"Rule {index + 1}", RuleForm.of(profile.rules[index]), index
            )
        elif tab == ENROLLMENT:
            self.push_screen(
                RuleEditor(
                    f"Exams the student is {ENROLLMENT_ROWS[key].lower()} to",
                    RuleForm.of_condition(getattr(profile.enrollment, key)),
                    self._match_counter(),
                    len(self._events),
                    with_kind=False,
                ),
                partial(self._enrollment_saved, key),
            )
        else:
            title = self._title_previews[int(key)].title
            titles = [item.title for item in self._title_previews]
            prefix = suggest_prefix(title, titles)
            self._open_rule_editor("New rule", RuleForm(value=prefix or title), None)

    def _open_rule_editor(self, title: str, form: RuleForm, index: int | None) -> None:
        draft = self.draft
        if draft is None:
            return
        self.push_screen(
            RuleEditor(title, form, self._match_counter(), len(self._events)),
            partial(self._rule_saved, index),
        )

    def _match_counter(self) -> partial[int]:
        return partial(count_matches, events=self._events)

    def _rule_saved(self, index: int | None, rule: Rule | None) -> None:
        if rule is None or self.draft is None:
            return
        rules = list(self.draft.session.profile.rules)
        if index is None:
            rules.append(rule)
        else:
            rules[index] = rule
        self._set_rules(rules, cursor=len(rules) - 1 if index is None else index)

    def _enrollment_saved(self, key: str, rule: Rule | None) -> None:
        if rule is None or self.draft is None:
            return
        profile = self.draft.session.profile
        profile.enrollment = replace(profile.enrollment, **{key: rule.condition})
        self._rules_changed()

    def action_delete_rule(self) -> None:
        draft = self.draft
        tab = self._rules_table()
        key = self._cursor_key(tab)
        if draft is None or key is None or tab == EVENTS:
            return
        profile = draft.session.profile
        if tab == ENROLLMENT:
            profile.enrollment = replace(profile.enrollment, **{key: None})
            self._rules_changed()
            return
        rules = list(profile.rules)
        del rules[int(key)]
        self._set_rules(rules, cursor=int(key))

    def action_move_rule(self, delta: int) -> None:
        key = self._cursor_key(RULES)
        if self.draft is None or key is None or self._rules_table() != RULES:
            return
        rules = list(self.draft.session.profile.rules)
        index = int(key)
        other = index + delta
        if not 0 <= other < len(rules):
            return
        rules[index], rules[other] = rules[other], rules[index]
        self._set_rules(rules, cursor=other)

    def _set_rules(self, rules: list[Rule], cursor: int) -> None:
        assert self.draft is not None
        self.draft.session.profile.rules = rules
        self._rules_changed()
        table = self._ui.query_one(f"#{RULES}-table", DataTable)
        if rules:
            table.move_cursor(row=min(cursor, len(rules) - 1))

    def _rules_changed(self) -> None:
        """Classifies the events again: courses, exams and deadlines change."""
        assert self.draft is not None
        self.draft.session = self.workflow.rediscover(self.draft.session)
        self._preferences_changed()

    def _show_scope_tabs(self) -> None:
        """Shows the tabs of the kinds of events the sync covers."""
        tabs = self._ui.query_one(TabbedContent)
        target = self.options.target
        for tab, shown in (
            (COURSES, target.includes_lectures),
            (EXAMS, target.includes_exams),
            (DEADLINES, target.includes_deadlines),
        ):
            if shown:
                tabs.show_tab(tab)
            else:
                tabs.hide_tab(tab)

    def on_select_changed(self, event: Select.Changed) -> None:
        if "scope" not in event.select.classes or not isinstance(
            event.value, SyncTarget
        ):
            return
        if event.value is self.options.target:
            return
        self.options = replace(self.options, target=event.value)
        for select in self._ui.query(".scope").results(Select):
            select.value = event.value  # the other selector follows
        self._show_scope_tabs()
        self._refresh_setup()
        if self.draft is not None:
            session = replace(self.draft.session, options=self.options)
            self.draft.session = self.workflow.rediscover(session)
            self._preferences_changed()

    # -- calendars -----------------------------------------------------------

    def _refresh_setup(self) -> None:
        source = self.setup.fixed_source or f"'{self.calendars.source}'"
        self._ui.query_one("#setup-source", Static).update(source)
        self._ui.query_one("#setup-target", Static).update(f"'{self.calendars.target}'")
        self._ui.query_one("#setup-time-zone", Static).update(
            Text.assemble(
                _time_zone_label(self.calendars.time_zone),
                (" · used when the target calendar is created", "dim"),
            )
        )
        if self.options.all_time:
            w_text = "All time"
        elif self.options.window_from and self.options.window_to:
            w_text = f"{self.options.window_from} → {self.options.window_to}"
        elif self.options.window_from:
            w_text = f"from {self.options.window_from}"
        elif self.options.window_to:
            w_text = f"until {self.options.window_to}"
        else:
            w_text = "All time"
        self._ui.query_one("#setup-window", Static).update(w_text)

        c_text = f"'{self.options.course}'" if self.options.course else "All courses"
        self._ui.query_one("#setup-course", Static).update(c_text)

        reminder_parts = [f"Scope: {SCOPE_LABELS[self.options.target]}"]
        if self.options.all_time:
            reminder_parts.append("Window: All time")
        elif self.options.window_from and self.options.window_to:
            reminder_parts.append(
                f"Window: {self.options.window_from} → {self.options.window_to}"
            )
        elif self.options.window_from:
            reminder_parts.append(f"Window: from {self.options.window_from}")
        elif self.options.window_to:
            reminder_parts.append(f"Window: until {self.options.window_to}")
        else:
            reminder_parts.append("Window: All time")

        if self.options.course:
            reminder_parts.append(f"Course: '{self.options.course}'")
        else:
            reminder_parts.append("Course: All")

        with contextlib.suppress(Exception):
            self._ui.query_one("#sync-scope-reminder", Static).update(
                " · ".join(reminder_parts)
            )
        notes = []
        if self.setup.fixed_source is not None:
            notes.append(
                "The events come from an iCal feed, which is set outside the "
                "profile: the source can't be changed here."
            )
        for role, variable in self.setup.overrides.items():
            notes.append(
                f"{variable} is set: it replaces the saved {role.value} calendar "
                "every time the tool starts."
            )
        self._ui.query_one("#setup-notes", Static).update("\n".join(notes))
        self.sub_title = self._base_sub_title + (
            " • unsaved changes"
            if self.draft is not None and self.draft.is_dirty
            else ""
        )

    @work(exclusive=True, group="calendar")
    async def choose_calendar(self, role: Role) -> None:
        calendars = await self._list_calendars()
        if calendars is not None:
            self.push_screen(
                self._calendar_picker(role, calendars),
                partial(self._calendar_chosen, role),
            )

    async def _list_calendars(self) -> list[CalendarInfo] | None:
        """The user's calendars (fetched once), or ``None`` if they can't be."""
        if self._calendar_list is None:
            self._set_busy(True)
            try:
                self._calendar_list = await asyncio.to_thread(
                    self.workflow.list_calendars
                )
            except Exception as exc:
                self._write_log("error", f"Could not list your calendars: {exc}")
                return None
            finally:
                self._set_busy(False)
        return self._calendar_list

    def _calendar_picker(
        self,
        role: Role,
        calendars: list[CalendarInfo],
        title: str | None = None,
        explanation: str = "",
        new_name: str = "",
    ) -> CalendarPicker:
        fixed = self.setup.fixed_source is not None
        choices = calendar_choices(calendars, role, self.calendars, fixed)
        if role is Role.SOURCE:
            return CalendarPicker(
                title or "Calendar to read from", choices, explanation=explanation
            )
        return CalendarPicker(
            title or "Calendar to write to",
            choices,
            partial(
                target_name_error,
                calendars=calendars,
                current=self.calendars,
                fixed_source=fixed,
            ),
            new_name=new_name,
            explanation=explanation,
        )

    # -- first run -----------------------------------------------------------

    @work(group="wizard")
    async def run_wizard(self) -> None:
        """Guided setup: source calendar, target calendar, rules, preview.

        Leaving a step (Escape) ends the guide: the app then works as usual,
        with what was chosen so far.
        """
        if not await self.push_screen_wait(WizardIntro()):
            self.load_session()
            return
        if self.setup.fixed_source is None:
            calendars = await self._list_calendars()
            source = None
            if calendars is not None:
                source = await self.push_screen_wait(
                    self._calendar_picker(
                        Role.SOURCE,
                        calendars,
                        title="Step 1 · The calendar with your timetable",
                        explanation=(
                            "The read-only calendar of your university, as you "
                            "subscribed to it in Google Calendar."
                        ),
                    )
                )
            # Picking the current source also saves it, creating the profile.
            await self.load_session(source_name=source).wait()
        else:
            await self.load_session().wait()
        if self.draft is None:
            return
        calendars = await self._list_calendars()
        if calendars is None:
            return
        target = await self.push_screen_wait(
            self._calendar_picker(
                Role.TARGET,
                calendars,
                title="Step 2 · The calendar to write the colored copy to",
                explanation=(
                    "Pick one of your calendars, or keep the new name: the "
                    "calendar is created when you apply the changes."
                ),
                new_name=f"{self.calendars.source} Colored",
            )
        )
        if target is None:
            return
        if target != self.calendars.target:
            self._commit_calendar(Role.TARGET, target)
        draft = self.draft
        choice = await self.push_screen_wait(
            WizardRules(draft.session.profile.name, self._title_previews)
        )
        tabs = self._ui.query_one(TabbedContent)
        if choice is RulesChoice.KEEP:
            self.notify("All set: review the changes, then press a to apply them.")
            self.action_preview()
            return
        if choice is RulesChoice.EMPTY:
            self._set_rules([], cursor=0)
        tabs.active = RULES
        self.notify(
            "Write the rules for your calendar: press n for a new rule, or Enter "
            "on an event. Then preview the changes with p."
        )

    def _calendar_chosen(self, role: Role, name: str | None) -> None:
        if name is None or name == getattr(self.calendars, role.value):
            return
        if role is Role.SOURCE:
            # Committed once its events are loaded.
            self.load_session(source_name=name)
        else:
            self._commit_calendar(role, name)

    def _commit_calendar(self, role: Role, name: str) -> None:
        self._commit_settings(
            {role.value: name}, f"The {role.value} calendar is now '{name}'."
        )
        self._discard_plan("Calendars changed: preview the changes again.")

    def _time_zone_chosen(self, value: str | None) -> None:
        if value is None:
            return
        time_zone = value.strip() or None
        if time_zone != self.calendars.time_zone:
            self._commit_settings(
                {"time_zone": time_zone},
                f"New calendars use {_time_zone_label(time_zone)}.",
            )

    def _window_chosen(self, value: str | None) -> None:
        if value is None:
            return
        d_from, d_to, all_t = _parse_window_string(value)
        self.options = replace(
            self.options, window_from=d_from, window_to=d_to, all_time=all_t
        )
        self._refresh_setup()
        self.load_session()
        self.notify("Sync window updated.")

    def _course_chosen(self, value: str | None) -> None:
        if value is None:
            return
        course = value.strip() or None
        self.options = replace(self.options, course=course)
        self._refresh_setup()
        if self.draft is not None:
            session = replace(self.draft.session, options=self.options)
            self.draft.session = self.workflow.rediscover(session)
            self._preferences_changed()
        self.notify("Course filter updated.")

    def _commit_settings(self, changes: dict[str, Any], message: str) -> None:
        """Uses the calendar settings from now on and saves them in the profile,
        without the preferences not saved yet."""
        assert self.profile is not None
        self.calendars = replace(self.calendars, **changes)
        saved = replace(self.profile.calendars, **changes)
        if self.draft is not None:
            profile = self.draft.set_calendars(saved)
            self.draft.session = replace(
                self.draft.session, target_name=self.calendars.target
            )
        else:
            self.profile.calendars = saved
            profile = self.profile
        try:
            self.workflow.save_profile(profile)
        except OSError as exc:
            self._write_log("error", f"Could not save the profile: {exc}")
        else:
            self.notify(message)
        self._refresh_setup()

    # -- phases (run in worker threads) --------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview":
            self.action_preview()
        elif event.button.id == "apply":
            self.action_apply()
        elif event.button.id == "change-source":
            self.choose_calendar(Role.SOURCE)
        elif event.button.id == "change-target":
            self.choose_calendar(Role.TARGET)
        elif event.button.id == "change-time-zone":
            self.push_screen(
                TextPrompt(
                    "Time zone of the target calendar, when it's created",
                    self.calendars.time_zone or "",
                    placeholder="e.g. Europe/Rome · empty: your primary calendar's",
                    validate=_optional_time_zone_error,
                ),
                self._time_zone_chosen,
            )
        elif event.button.id == "change-window":
            current_val = (
                "all"
                if self.options.all_time
                else (
                    f"{self.options.window_from}..{self.options.window_to}"
                    if (self.options.window_from and self.options.window_to)
                    else ""
                )
            )
            self.push_screen(
                TextPrompt(
                    "Sync window ('all', 'semester', or 'YYYY-MM-DD..YYYY-MM-DD')",
                    current_val,
                    placeholder="e.g. 2026-03-01..2026-09-15 · 'all' · 'semester'",
                    validate=_validate_window_input,
                ),
                self._window_chosen,
            )
        elif event.button.id == "change-course":
            self.push_screen(
                TextPrompt(
                    "Course filter (empty for all courses)",
                    self.options.course or "",
                    placeholder="e.g. Algorithms · empty: all courses",
                ),
                self._course_chosen,
            )

    def action_preview(self) -> None:
        if not self.busy and self.draft is not None:
            self._ui.query_one(TabbedContent).active = SYNC
            self.preview_changes()

    def action_apply(self) -> None:
        if not self.busy and self.plan is not None:
            self._ui.query_one(TabbedContent).active = SYNC
            self.apply_changes()

    @work(exclusive=True, group="calendar")
    async def load_session(self, source_name: str | None = None) -> None:
        """Loads the events, from the calendar ``source_name`` if given (then
        the new source calendar), keeping the edits not saved yet."""
        source = (
            self.source if source_name is None else self.setup.source_for(source_name)
        )
        self._set_busy(True)
        for table in self._ui.query(DataTable):
            table.loading = True
        try:
            if self.profile is None:
                self.profile = await asyncio.to_thread(self.workflow.load_profile)
            session = await asyncio.to_thread(
                self.workflow.load,
                self.options,
                source,
                self.calendars.target,
                self.profile,
            )
        except SourceCalendarNotFoundError as exc:
            self._write_log("error", f"{exc} Choose it in the Setup tab.")
            self._ui.query_one(TabbedContent).active = SETUP
        except Exception as exc:
            self._write_log("error", f"Could not load the source events: {exc}")
        else:
            if source_name is not None:
                self.source = source
                self._commit_calendar(Role.SOURCE, source_name)
            saved = self.draft.saved_profile if self.draft is not None else None
            self.draft = PreferencesDraft(session, saved)
            self._refresh_tables()
            self._refresh_setup()
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
        progress = self._ui.query_one(SyncProgress)
        progress.start(plan)
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
            progress.stop()
            self._set_busy(False)
            self._refresh_tables()
        self._set_plan(None)
        self._ui.query_one("#plan-summary", Static).update(_result_headline(result))
        self._ui.query_one(PlanTree).clear()

    def _report_progress(self, completed: int, total: int) -> None:
        self.post_message(ApplyProgress(completed, total))

    def on_apply_progress(self, message: ApplyProgress) -> None:
        self._ui.query_one(SyncProgress).advance(message.completed)
