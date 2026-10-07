"""Reusable TUI pieces: color swatches, the color and calendar pickers, the
rule editor and the plan view."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    Tree,
)
from textual.widgets.option_list import Option

from calendar_coloring.events import Event
from calendar_coloring.palette import GoogleColor
from calendar_coloring.rules import (
    NAME_PLACEHOLDER,
    TITLE_PLACEHOLDER,
    Condition,
    EventKind,
    Field,
    MatchKind,
    Rule,
)
from calendar_coloring.sync.models import (
    ColorOrigin,
    EventDecision,
    Mutation,
    MutationAction,
    SyncPlan,
)
from calendar_coloring.tui.rules import RuleForm
from calendar_coloring.tui.setup import CalendarChoice

ACTION_STYLES = {
    MutationAction.INSERT: ("+", "Insert", "green"),
    MutationAction.UPDATE: ("~", "Update", "yellow"),
    MutationAction.DELETE: ("-", "Delete", "red"),
}

_COLOR_ORIGINS = {
    ColorOrigin.STRATEGY: "from your preferences",
    ColorOrigin.PRESERVED: "kept from the target event",
    ColorOrigin.DEFAULT: "calendar default",
}


def swatch(color: GoogleColor | None, label: bool = True) -> Text:
    """A truecolor block followed by the color name (``—`` for no color)."""
    if color is None:
        return Text("   — none", style="dim")
    text = Text()
    text.append("   ", style=f"on {color.hex}")
    if label:
        text.append(f" {color.label}")
    return text


class ColorPicker(ModalScreen[GoogleColor | None]):
    """Modal list of the 11 Google colors. Dismisses with the chosen color, or
    ``None`` when cancelled."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ColorPicker {
        align: center middle;
    }
    ColorPicker > Vertical {
        width: 44;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    ColorPicker OptionList {
        height: auto;
        max-height: 15;
    }
    """

    def __init__(self, subject: str, current: GoogleColor | None = None) -> None:
        super().__init__()
        self.subject = subject
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text.assemble("Color for ", (self.subject, "bold")))
            yield OptionList(
                *(
                    Option(
                        swatch(color).append(f"  ({color.color_id})", style="dim"),
                        id=color.color_id,
                    )
                    for color in GoogleColor
                )
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        if self.current is not None:
            options.highlighted = list(GoogleColor).index(self.current)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(GoogleColor.from_id(str(event.option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)


class CalendarPicker(ModalScreen[str | None]):
    """Modal list of calendars. With ``validate_new``, a new calendar name can
    also be typed. Dismisses with the chosen name, or ``None`` when cancelled."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    CalendarPicker {
        align: center middle;
    }
    CalendarPicker > Vertical {
        width: 64;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    CalendarPicker OptionList {
        height: auto;
        max-height: 16;
    }
    CalendarPicker #calendar-error {
        color: $error;
    }
    """

    def __init__(
        self,
        title: str,
        choices: Sequence[CalendarChoice],
        validate_new: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.choices = list(choices)
        self.validate_new = validate_new

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.title_text, style="bold"))
            if self.choices:
                yield OptionList(
                    *(
                        Option(
                            Text.assemble(
                                choice.name,
                                (f"  {choice.note}" if choice.note else "", "dim"),
                            ),
                            id=str(index),
                            disabled=not choice.allowed,
                        )
                        for index, choice in enumerate(self.choices)
                    )
                )
            else:
                yield Label(Text("No calendars found.", style="dim"))
            if self.validate_new is not None:
                yield Input(
                    placeholder="…or type the name of a new calendar",
                    id="new-calendar",
                )
                yield Label("", id="calendar-error")

    def on_mount(self) -> None:
        current = next(
            (i for i, c in enumerate(self.choices) if c.note.startswith("current")),
            None,
        )
        if self.choices:
            options = self.query_one(OptionList)
            if current is not None:
                options.highlighted = current
            options.focus()
        elif self.validate_new is not None:
            self.query_one(Input).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.choices[int(str(event.option.id))].name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        assert self.validate_new is not None
        error = self.validate_new(event.value)
        if error is not None:
            self.query_one("#calendar-error", Label).update(error)
            return
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


KIND_LABELS = {
    EventKind.EXAM: "Exam",
    EventKind.LECTURE: "Lecture",
    EventKind.DEADLINE: "Deadline",
}
_FIELD_OPTIONS = [
    ("Title", Field.TITLE),
    ("Description", Field.DESCRIPTION),
    ("Location", Field.LOCATION),
    ("Category (iCal)", Field.CATEGORY),
]
_MATCH_OPTIONS = [
    ("starts with", MatchKind.STARTS_WITH),
    ("contains", MatchKind.CONTAINS),
    ("is exactly", MatchKind.EQUALS),
    ("matches the regex", MatchKind.REGEX),
]


class RuleEditor(ModalScreen[Rule | None]):
    """Modal form for a rule, telling live how many events it matches.

    With ``with_kind=False`` only the condition is edited (e.g. for the exam
    enrollment): the returned rule's kind and title are then meaningless.
    Dismisses with the rule, or ``None`` when cancelled.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    DEFAULT_CSS = """
    RuleEditor {
        align: center middle;
    }
    RuleEditor > Vertical {
        width: 76;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    RuleEditor .form-label {
        margin-top: 1;
        color: $text-muted;
    }
    RuleEditor Horizontal {
        height: auto;
    }
    RuleEditor #rule-field, RuleEditor #rule-match {
        width: 1fr;
    }
    RuleEditor #rule-status {
        margin: 1 0;
    }
    RuleEditor #rule-status.error {
        color: $error;
    }
    RuleEditor #rule-buttons Button {
        margin-right: 2;
    }
    """

    def __init__(
        self,
        title: str,
        form: RuleForm,
        count_matches: Callable[[Condition], int],
        total: int,
        with_kind: bool = True,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.form = form
        self.count_matches = count_matches
        self.total = total
        self.with_kind = with_kind
        self.result: Rule | None = None

    def compose(self) -> ComposeResult:
        form = self.form
        with Vertical():
            yield Label(Text(self.title_text, style="bold"))
            if self.with_kind:
                yield Label("Events matching the condition are", classes="form-label")
                yield Select(
                    [(label, kind) for kind, label in KIND_LABELS.items()],
                    value=form.kind,
                    allow_blank=False,
                    id="rule-kind",
                )
            yield Label("Condition", classes="form-label")
            with Horizontal():
                yield Select(
                    _FIELD_OPTIONS,
                    value=form.field,
                    allow_blank=False,
                    id="rule-field",
                )
                yield Select(
                    _MATCH_OPTIONS,
                    value=form.match,
                    allow_blank=False,
                    id="rule-match",
                )
            yield Input(form.value, placeholder="Text to match", id="rule-value")
            yield Checkbox(
                "Ignore upper/lower case", form.ignore_case, id="rule-ignore-case"
            )
            if self.with_kind:
                yield Label(
                    f"Title in the colored calendar ({TITLE_PLACEHOLDER}: the "
                    f"original, {NAME_PLACEHOLDER}: the name)",
                    classes="form-label",
                )
                yield Input(form.title, id="rule-title")
            yield Static(id="rule-status")
            with Horizontal(id="rule-buttons"):
                yield Button("Save", id="save-rule", variant="primary")
                yield Button("Cancel", id="cancel-rule")

    def on_mount(self) -> None:
        self.query_one("#rule-value", Input).focus()
        self._validate()

    def _validate(self) -> None:
        status = self.query_one("#rule-status", Static)
        try:
            condition = Condition(
                field=self.query_one("#rule-field", Select).value,  # type: ignore[arg-type]
                match=self.query_one("#rule-match", Select).value,  # type: ignore[arg-type]
                value=self.query_one("#rule-value", Input).value,
                ignore_case=self.query_one("#rule-ignore-case", Checkbox).value,
            )
        except ValueError as exc:
            self.result = None
            status.update(f"Invalid condition: {exc}.")
            status.add_class("error")
        else:
            kind = (
                self.query_one("#rule-kind", Select).value
                if self.with_kind
                else self.form.kind
            )
            title = (
                self.query_one("#rule-title", Input).value.strip()
                if self.with_kind
                else ""
            )
            self.result = Rule(
                kind,  # type: ignore[arg-type]
                condition,
                title or TITLE_PLACEHOLDER,
            )
            matches = self.count_matches(condition)
            status.update(f"Matches {matches} of {self.total} events.")
            status.remove_class("error")
        self.query_one("#save-rule", Button).disabled = self.result is None

    def on_input_changed(self, event: Input.Changed) -> None:
        self._validate()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._validate()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._validate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "save-rule":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        if self.result is not None:
            self.dismiss(self.result)

    def action_cancel(self) -> None:
        self.dismiss(None)


def plan_summary(plan: SyncPlan) -> Text:
    text = Text()
    for action, (sign, label, style) in ACTION_STYLES.items():
        text.append(f"{sign} {plan.count(action)} {label.lower()}  ", style=style)
    text.append(f"· {plan.unchanged} unchanged", style="dim")
    notes = []
    if plan.target_calendar_id is None:
        notes.append("The target calendar will be created.")
    if plan.pruned:
        notes.append(
            f"{plan.pruned} event(s) before {plan.prune_before} will be pruned."
        )
    if plan.preserved_unmanaged:
        notes.append(
            f"{len(plan.preserved_unmanaged)} event(s) you added yourself are "
            "left untouched."
        )
    if plan.skipped_without_start:
        notes.append(
            f"{len(plan.skipped_without_start)} source event(s) without a start "
            "time are skipped."
        )
    for note in notes:
        text.append(f"\n{note}", style="italic")
    return text


def _when(event: Event) -> str:
    def edge(name: str) -> str:
        value = event.get(name) or {}
        return str(value.get("dateTime", value.get("date", "?")))

    return f"{edge('start')} → {edge('end')}"


def _mutation_details(mutation: Mutation, decision: EventDecision | None) -> list[Text]:
    body = mutation.body or {}
    color = GoogleColor.parse(body.get("colorId"))
    origin = _COLOR_ORIGINS[decision.color_origin] if decision else ""
    details = [
        Text.assemble("Color: ", swatch(color), (f"  {origin}", "dim")),
        Text(f"When: {_when(body)}"),
    ]
    if decision is not None and decision.source_summary != decision.target_summary:
        details.append(Text(f"Source title: {decision.source_summary}"))
    if body.get("location"):
        details.append(Text(f"Location: {body['location']}"))
    if body.get("recurrence"):
        details.append(Text("Recurring event"))
    return details


class PlanTree(Tree[None]):
    """The mutations of a plan grouped by action, expandable per event."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__("Changes", id=id)
        self.show_root = False

    def show_plan(self, plan: SyncPlan) -> None:
        self.clear()
        self.root.expand()
        decisions = {d.event_id: d for d in plan.decisions}
        for action, (sign, label, style) in ACTION_STYLES.items():
            mutations = [m for m in plan.mutations if m.action is action]
            if not mutations:
                continue
            group = self.root.add(
                Text(f"{sign} {label} ({len(mutations)})", style=f"bold {style}"),
                expand=True,
            )
            for mutation in mutations:
                if action is MutationAction.DELETE:
                    group.add_leaf(mutation.summary)
                    continue
                node = group.add(mutation.summary)
                for detail in _mutation_details(
                    mutation, decisions.get(mutation.event_id)
                ):
                    node.add_leaf(detail)
